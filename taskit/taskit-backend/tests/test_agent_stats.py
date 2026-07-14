"""Tests for the shared agent-stats library (BACKEND side).

After extracting percentile / is_operator_email from
testing_tools/autonomy_metrics.py into the shared
tasks.agent_stats module, these tests pin behavior at the new
extraction site — the boundary the suggester consumes.

Coverage:
  PercentileMath — same math, new home (regression).
  OperatorEmailHeuristic — same rule, new home (regression).
  AgentStatsAggregation — per-agent rollups from a list of per-task
    outcomes (the shared data shape consumed by the odin suggester).
  ComputeAgentStatsFromDB — pulls outcomes from the live ORM and
    aggregates into AgentStats, mirroring the wave-1/wave-2 fixture
    counts (proves the refactor didn't change the numbers).
"""

import os
import sys
from pathlib import Path

# Force SQLite + Django settings before importing anything project-side.
os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

import django  # noqa: E402

django.setup()

from django.test import SimpleTestCase, TestCase  # noqa: E402

from tasks.agent_stats import (  # noqa: E402
    AgentStats,
    aggregate_agent_stats,
    compute_agent_stats_for_board,
    is_operator_email,
    percentile,
)
from tasks.models import (  # noqa: E402
    Board,
    Spec,
    Task,
    TaskStatus,
    User,
    UserRole,
)


# ── Pure-function regression (same behavior as the old autonomy_metrics) ────


class PercentileMath(SimpleTestCase):
    """Same math as the original autonomy_metrics.percentile, re-pinned."""

    def test_percentile_known_values(self):
        # 1..10 -> p50=5.5, p90=9.1
        vals = list(range(1, 11))
        self.assertAlmostEqual(percentile(vals, 50), 5.5, places=1)
        self.assertAlmostEqual(percentile(vals, 90), 9.1, places=1)

    def test_percentile_short_inputs(self):
        self.assertEqual(percentile([42], 50), 42)
        self.assertEqual(percentile([], 50), 0)


class OperatorEmailHeuristic(SimpleTestCase):
    """Same rule as the original autonomy_metrics.is_operator_email."""

    def test_agent_email_is_not_operator(self):
        self.assertFalse(is_operator_email("claude@odin.agent"))
        self.assertFalse(is_operator_email("minimax+opus@odin.agent"))

    def test_human_email_is_operator(self):
        self.assertTrue(is_operator_email("alice@example.com"))
        self.assertTrue(is_operator_email("bob@test.com"))

    def test_system_is_not_operator(self):
        self.assertFalse(is_operator_email("system@taskit"))

    def test_empty_email_is_operator(self):
        self.assertTrue(is_operator_email(""))

    def test_machine_domain_classes_are_not_operators(self):
        """W248: by-domain-class denylist. Live data showed @system,
        @harness.kit, and @taskit authors (twins memory, trace sidecar,
        proof upload) being misclassified as operators because the prior
        rule only excluded @odin.agent + system@taskit."""
        self.assertFalse(is_operator_email("odin+memory@system"))
        self.assertFalse(is_operator_email("odin+dag-executor@system"))
        self.assertFalse(is_operator_email("odin@harness.kit"))
        self.assertFalse(is_operator_email("proof-upload@taskit"))

    def test_merge_agent_odin_is_not_operator(self):
        """merge-agent@odin is an internal merge-resolution service, not a
        human. The DAG executor posts merge-conflict comments with this
        identity; counting them as operator touches inflates the human-touch
        number and makes the L2 counter under-report autonomy."""
        self.assertFalse(is_operator_email("merge-agent@odin"))

    def test_known_human_is_operator(self):
        """W248: the documented human operator is operator@."""
        self.assertTrue(is_operator_email("operator@example.com"))

    def test_is_human_author_alias(self):
        """is_human_author is the positive form; is_operator_email is
        the same callable. Both names live so existing callers + tests
        keep working without churn."""
        from tasks.agent_stats import is_human_author
        self.assertIs(is_human_author, is_operator_email)
        # Sanity: same answer for the live author set.
        self.assertTrue(is_human_author("operator@example.com"))
        self.assertFalse(is_human_author("odin+memory@system"))


# ── Pure-function aggregation ───────────────────────────────────────


class AgentStatsAggregation(SimpleTestCase):
    """aggregate_agent_stats turns a list of (agent, success, tokens)
    outcomes into per-agent AgentStats."""

    def _out(self, agent, success, tokens):
        return {"agent": agent, "success": success, "tokens": tokens}

    def test_per_agent_rollup(self):
        """Success_count, sample_count, success_rate, median_tokens."""
        outcomes = [
            self._out("gemini", True, 1000),
            self._out("gemini", True, 2000),
            self._out("gemini", False, 3000),
            self._out("claude", True, 8000),
            self._out("claude", True, 12000),
            self._out("qwen", False, 0),
        ]
        stats = aggregate_agent_stats(outcomes)
        assert set(stats.keys()) == {"gemini", "claude", "qwen"}
        gemini = stats["gemini"]
        assert gemini.sample_count == 3
        assert gemini.success_count == 2
        assert abs(gemini.success_rate - (2 / 3)) < 1e-9
        assert gemini.median_tokens == 2000  # 1000, 2000, 3000
        claude = stats["claude"]
        assert claude.sample_count == 2
        assert claude.success_count == 2
        assert claude.median_tokens == 10000  # 8000, 12000
        qwen = stats["qwen"]
        assert qwen.sample_count == 1
        assert qwen.success_rate == 0.0

    def test_empty_outcomes_returns_empty(self):
        assert aggregate_agent_stats([]) == {}

    def test_median_uses_only_observed_tokens(self):
        """A 0-token task is a capture gap, not a real measurement —
        the median must reflect the observed values, not be dragged
        down toward the gap."""
        outcomes = [
            self._out("gemini", True, 4000),
            self._out("gemini", False, 0),  # capture gap, must NOT drag median
        ]
        stats = aggregate_agent_stats(outcomes)
        # Median over [4000] = 4000. The single observed token count
        # is the honest answer; the gap is recorded via the success
        # field, not by polluting the median.
        assert stats["gemini"].median_tokens == 4000
        assert stats["gemini"].sample_count == 2


# ── DB-backed aggregation ────────────────────────────────────────────


def _make_agent(name):
    return User.objects.create(
        email=f"{name}@odin.agent",
        name=name,
        role=UserRole.AGENT,
        is_admin=False,
    )


class ComputeAgentStatsFromDB(TestCase):
    """compute_agent_stats_for_board reads DONE tasks, classifies each,
    and aggregates per agent. This is the data path that the new
    /boards/{id}/agent-stats/ REST endpoint exposes and that the odin
    suggester consumes."""

    def setUp(self):
        self.board = Board.objects.create(
            name="Routing Stats Test Board", working_dir="/tmp/rt"
        )
        self.spec = Spec.objects.create(
            board=self.board,
            odin_id="sp_routing_stats",
            title="Routing Stats Spec",
        )
        self.gemini = _make_agent("gemini")
        self.claude = _make_agent("claude")

    def test_counts_match_classifier(self):
        """10 gemini tasks of which 8 are agent-authored → 80% success.
        Mirrors autonomy_metrics.classify_task + the wave-1 fixture.

        The 2 operator-disqualified tasks still count as observed
        samples (a closed-but-stolen-back merge isn't free of signal —
        it's a FAIL cycle from the agent's perspective)."""
        for i in range(10):
            t = Task.objects.create(
                board=self.board,
                spec=self.spec,
                title=f"Gemini-{i}",
                status=TaskStatus.DONE,
                created_by="gemini@odin.agent",
                assignee=self.gemini,
                metadata={"last_duration_ms": 60_000, "last_usage": {"total_tokens": 4000}},
            )
            from tasks.models import TaskHistory, TaskComment
            TaskHistory.objects.create(
                task=t, field_name="status",
                old_value="TODO", new_value="EXECUTING",
                changed_by="system@taskit",
            )
            if i < 8:
                # Agent drove it to DONE
                TaskHistory.objects.create(
                    task=t, field_name="status",
                    old_value="EXECUTING", new_value="DONE",
                    changed_by="gemini@odin.agent",
                )
            else:
                # Operator flip → disqualifying (counts as observed, but not success)
                TaskHistory.objects.create(
                    task=t, field_name="status",
                    old_value="EXECUTING", new_value="DONE",
                    changed_by="alice@example.com",
                )

        stats = compute_agent_stats_for_board(self.board)
        assert "gemini" in stats
        gemini_stats: AgentStats = stats["gemini"]
        assert gemini_stats.sample_count == 10
        assert gemini_stats.success_count == 8
        assert abs(gemini_stats.success_rate - 0.8) < 1e-9
        assert gemini_stats.median_tokens == 4000

    def test_in_progress_tasks_excluded(self):
        """Only DONE tasks count toward rollups — the rest are noise."""
        # 2 DONE (gemini), 1 IN_PROGRESS (gemini). Expect sample=2.
        for i in range(2):
            t = Task.objects.create(
                board=self.board,
                spec=self.spec,
                title=f"Done-{i}",
                status=TaskStatus.DONE,
                created_by="gemini@odin.agent",
                assignee=self.gemini,
                metadata={"last_duration_ms": 60_000, "last_usage": {"total_tokens": 5000}},
            )
            from tasks.models import TaskHistory
            TaskHistory.objects.create(
                task=t, field_name="status",
                old_value="TODO", new_value="DONE",
                changed_by="gemini@odin.agent",
            )
        Task.objects.create(
            board=self.board,
            spec=self.spec,
            title="In-progress",
            status=TaskStatus.IN_PROGRESS,
            created_by="gemini@odin.agent",
            assignee=self.gemini,
            metadata={},
        )
        stats = compute_agent_stats_for_board(self.board)
        assert stats["gemini"].sample_count == 2

    def test_no_done_tasks_returns_empty(self):
        assert compute_agent_stats_for_board(self.board) == {}


# ── Re-export compatibility ─────────────────────────────────────────


class AutonomyMetricsReExports(SimpleTestCase):
    """testing_tools.autonomy_metrics must re-export the moved helpers
    so existing tests keep passing without code change."""

    def test_old_import_paths_still_work(self):
        # autonomy_metrics is a sibling script under testing_tools/ —
        # add it to sys.path so the import shape that
        # test_autonomy_metrics.py uses still works.
        testing_tools_dir = str(
            Path(__file__).resolve().parent.parent / "testing_tools"
        )
        if testing_tools_dir not in sys.path:
            sys.path.insert(0, testing_tools_dir)
        from autonomy_metrics import is_operator_email as old_op  # noqa: F401
        from autonomy_metrics import percentile as old_pct  # noqa: F401
        assert old_op is is_operator_email
        assert old_pct is percentile


# ── REST endpoint ───────────────────────────────────────────────────


class AgentStatsRESTEndpoint(TestCase):
    """The /boards/{id}/agent-stats/ endpoint returns the same
    shape that odin's ``fetch_agent_stats`` consumes.

    Auth is disabled in tests (FIREBASE_AUTH_ENABLED=False), so we
    can hit the endpoint directly via the test client."""

    def setUp(self):
        self.board = Board.objects.create(
            name="REST Stats Board", working_dir="/tmp/rest-stats"
        )

    def _make_agent_done(self, agent_name, success, index=0, tokens=4000):
        spec, _ = Spec.objects.get_or_create(
            board=self.board,
            odin_id=f"sp_{agent_name}",
            defaults={"title": f"{agent_name} spec"},
        )
        agent, _ = User.objects.get_or_create(
            email=f"{agent_name}@odin.agent",
            defaults={
                "name": agent_name,
                "role": UserRole.AGENT,
                "is_admin": False,
            },
        )
        t = Task.objects.create(
            board=self.board,
            spec=spec,
            title=f"{agent_name} task {index}",
            status=TaskStatus.DONE,
            created_by=f"{agent_name}@odin.agent",
            assignee=agent,
            metadata={
                "last_duration_ms": 60_000,
                "last_usage": {"total_tokens": tokens},
            },
        )
        from tasks.models import TaskHistory
        TaskHistory.objects.create(
            task=t, field_name="status",
            old_value="TODO", new_value="DONE",
            changed_by=(
                f"{agent_name}@odin.agent" if success
                else "alice@example.com"
            ),
        )

    def test_endpoint_returns_rows_with_expected_fields(self):
        self._make_agent_done("gemini", success=True, index=1)
        self._make_agent_done("gemini", success=True, index=2)
        resp = self.client.get(f"/boards/{self.board.id}/agent-stats/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("agents", data)
        # gemini is the only observed agent; one row.
        self.assertEqual(len(data["agents"]), 1)
        row = data["agents"][0]
        for field in ("name", "sample_count", "success_count",
                      "success_rate", "median_tokens"):
            self.assertIn(field, row)
        self.assertEqual(row["name"], "gemini")
        self.assertEqual(row["sample_count"], 2)
        self.assertEqual(row["success_count"], 2)
        self.assertEqual(row["median_tokens"], 4000)

    def test_endpoint_returns_empty_for_board_with_no_done_tasks(self):
        resp = self.client.get(f"/boards/{self.board.id}/agent-stats/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["agents"], [])

    def test_endpoint_scopes_to_spec(self):
        """When ?spec= is supplied, only tasks for that spec roll up."""
        spec_a = Spec.objects.create(
            board=self.board, odin_id="sp_a", title="Spec A",
        )
        spec_b = Spec.objects.create(
            board=self.board, odin_id="sp_b", title="Spec B",
        )
        gemini = User.objects.create(
            email="gemini@odin.agent", name="gemini",
            role=UserRole.AGENT, is_admin=False,
        )
        from tasks.models import TaskHistory
        for spec, label in [(spec_a, "a"), (spec_b, "b")]:
            t = Task.objects.create(
                board=self.board,
                spec=spec,
                title=f"task-{label}",
                status=TaskStatus.DONE,
                created_by="gemini@odin.agent",
                assignee=gemini,
                metadata={"last_usage": {"total_tokens": 1000}},
            )
            TaskHistory.objects.create(
                task=t, field_name="status",
                old_value="TODO", new_value="DONE",
                changed_by="gemini@odin.agent",
            )
        resp = self.client.get(
            f"/boards/{self.board.id}/agent-stats/?spec={spec_a.id}"
        )
        data = resp.json()
        self.assertEqual(len(data["agents"]), 1)
        self.assertEqual(data["agents"][0]["sample_count"], 1)

    def test_endpoint_404_for_unknown_spec(self):
        resp = self.client.get(
            f"/boards/{self.board.id}/agent-stats/?spec=9999"
        )
        self.assertEqual(resp.status_code, 404)
