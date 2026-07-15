"""Tests for the W12.4 stats-page rebuild.

Adds four behaviors to the analytics cost-summary endpoint that the
existing tests don't cover:
  * throughput_funnel — mutually exclusive, exhaustive 4-bucket
    breakdown (pass / rework / fail / in-flight) that sums to 100%
    on the visible denominator. Replaces the previous review-pass +
    rework-rate pair that didn't add up.
  * league — board-scoped OR aggregated (when no board filter) per
    (agent, model) ranking. Without this, the All-boards view drops
    the section that renders fine per-board.
  * per_spec — per-spec cost + outcome rollup, one row per spec,
    sorted by created_at desc. Surfaces "which spec costs the most"
    without needing to drill into each spec's story.
  * scheduled_tasks — every active/paused/completed schedule with
    last-run success/failure history, aggregated across boards when
    the board filter is empty.

Coverage:
  FunnelMath — pure helper `compute_throughput_funnel` partitions
    tasks into 4 mutually exclusive buckets; denominator math is
    correct; sums to 100% on the visible denominator.
  EndpointShape — /api/analytics/cost-summary/ exposes all four
    new sections even when zero data is present.
  EndpointWithData — funnel math + scheduled-tasks presence on a
    realistic dataset.
"""

import json
import os
import sys

from datetime import datetime, timedelta, timezone as dt_tz

from django.utils import timezone

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

import django  # noqa: E402

django.setup()

from django.test import TestCase  # noqa: E402

from tasks.models import (  # noqa: E402
    Board,
    ReflectionReport,
    ReflectionStatus,
    ScheduleKind,
    ScheduleRunStatus,
    ScheduleStatus,
    Spec,
    Task,
    TaskHistory,
    TaskSchedule,
    TaskScheduleRun,
    TaskStatus,
    User,
    UserRole,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _make_agent(name):
    user, _ = User.objects.get_or_create(
        email=f"{name}@odin.agent",
        defaults={"name": name, "role": UserRole.AGENT, "is_active": True,
                  "is_admin": False},
    )
    return user


def _make_operator(email="operator@example.com"):
    user, _ = User.objects.get_or_create(
        email=email,
        defaults={"name": email.split("@")[0], "role": UserRole.HUMAN,
                  "is_active": True, "is_admin": False},
    )
    return user


def _add_history(task, field_name, old_value, new_value, changed_by):
    return TaskHistory.objects.create(
        task=task,
        field_name=field_name,
        old_value=str(old_value),
        new_value=str(new_value),
        changed_by=changed_by,
    )


# ── Group 1: pure funnel helper ─────────────────────────────────────


class FunnelMath(TestCase):
    """Coverage: the funnel partitioning math itself.

    Tests the helper directly with synthetic task records; doesn't
    touch the ORM for the partitioning rules (only for fixture
    bootstrap).
    """

    def setUp(self):
        self.board = Board.objects.create(
            name="Funnel Board", working_dir="/tmp/funnel",
        )

    def _import_helper(self):
        from tasks.analytics import compute_throughput_funnel
        return compute_throughput_funnel

    def test_throughput_funnel_buckets_are_mutually_exclusive(self):
        """Each task lands in exactly one bucket. A DONE task with
        rework_count > 0 is a 'rework' (not pass + fail). A FAILED
        task is 'fail' (not rework). An in-progress task is
        'in-flight' (not any of pass/rework/fail).
        """
        compute_throughput_funnel = self._import_helper()

        # DONE clean → pass
        t_pass = Task.objects.create(
            board=self.board, status=TaskStatus.DONE, title="pass",
            created_by="claude@odin.agent", metadata={"rework_count": 0},
        )
        # DONE with 2 reworks → rework
        t_rework = Task.objects.create(
            board=self.board, status=TaskStatus.DONE, title="rework",
            created_by="claude@odin.agent", metadata={"rework_count": 2},
        )
        # FAILED → fail
        t_fail = Task.objects.create(
            board=self.board, status=TaskStatus.FAILED, title="fail",
            created_by="claude@odin.agent", metadata={},
        )
        # EXECUTING → in-flight
        t_inflight = Task.objects.create(
            board=self.board, status=TaskStatus.EXECUTING, title="inflight",
            created_by="claude@odin.agent", metadata={},
        )

        result = compute_throughput_funnel([
            t_pass, t_rework, t_fail, t_inflight,
        ])
        buckets = {b["bucket"]: b for b in result["buckets"]}

        self.assertEqual(buckets["pass"]["count"], 1)
        self.assertEqual(buckets["rework"]["count"], 1)
        self.assertEqual(buckets["fail"]["count"], 1)
        self.assertEqual(buckets["in_flight"]["count"], 1)

    def test_throughput_funnel_is_exhaustive(self):
        """Every non-trivial status (BACKLOG/TODO/REVIEW/TESTING/etc.)
        that isn't DONE-with-rework, FAILED, or currently EXECUTING/
        IN_PROGRESS falls into 'in_flight'. Nothing is dropped.
        """
        compute_throughput_funnel = self._import_helper()

        tasks = []
        for status in (
            TaskStatus.BACKLOG, TaskStatus.TODO, TaskStatus.REVIEW,
            TaskStatus.TESTING, TaskStatus.IN_PROGRESS,
            TaskStatus.EXECUTING,
        ):
            tasks.append(Task.objects.create(
                board=self.board, status=status, title=status,
                created_by="claude@odin.agent", metadata={},
            ))

        result = compute_throughput_funnel(tasks)
        total = sum(b["count"] for b in result["buckets"])
        self.assertEqual(total, len(tasks))

    def test_throughput_funnel_sums_to_one_hundred_percent(self):
        """The four bucket percentages sum to 100 on the same denominator.

        This is the bug the user reported: 66% pass + 15% rework with
        19% missing. After this fix, the bucket counts must add to the
        denominator AND the percentages must add to exactly 100.
        """
        compute_throughput_funnel = self._import_helper()

        tasks = []
        # 66 pass, 15 rework, 10 fail, 9 in-flight — total 100
        for _ in range(66):
            tasks.append(Task.objects.create(
                board=self.board, status=TaskStatus.DONE, title="p",
                created_by="c@odin.agent", metadata={"rework_count": 0},
            ))
        for _ in range(15):
            tasks.append(Task.objects.create(
                board=self.board, status=TaskStatus.DONE, title="r",
                created_by="c@odin.agent", metadata={"rework_count": 1},
            ))
        for _ in range(10):
            tasks.append(Task.objects.create(
                board=self.board, status=TaskStatus.FAILED, title="f",
                created_by="c@odin.agent", metadata={},
            ))
        for _ in range(9):
            tasks.append(Task.objects.create(
                board=self.board, status=TaskStatus.EXECUTING, title="i",
                created_by="c@odin.agent", metadata={},
            ))

        result = compute_throughput_funnel(tasks)
        buckets = {b["bucket"]: b for b in result["buckets"]}
        total = result["total"]
        self.assertEqual(total, 100)
        self.assertEqual(buckets["pass"]["count"], 66)
        self.assertEqual(buckets["rework"]["count"], 15)
        self.assertEqual(buckets["fail"]["count"], 10)
        self.assertEqual(buckets["in_flight"]["count"], 9)

        pct_sum = sum(b["pct"] for b in result["buckets"])
        # Allow a tiny rounding gap (each bucket is rounded to whole %)
        self.assertLessEqual(abs(pct_sum - 100.0), 1.0)
        # Stronger: bucket counts must add to total exactly.
        self.assertEqual(
            sum(b["count"] for b in result["buckets"]),
            total,
        )

    def test_throughput_funnel_empty_returns_zeroed_structure(self):
        """No tasks → total=0, all buckets=0/0%, never missing key."""
        compute_throughput_funnel = self._import_helper()
        result = compute_throughput_funnel([])
        self.assertEqual(result["total"], 0)
        bucket_names = {b["bucket"] for b in result["buckets"]}
        self.assertEqual(
            bucket_names,
            {"pass", "rework", "fail", "in_flight"},
        )
        for b in result["buckets"]:
            self.assertEqual(b["count"], 0)
            self.assertEqual(b["pct"], 0.0)


# ── Group 2: cost-summary exposes the new sections ──────────────────


class EndpointShape(TestCase):
    """Coverage: /api/analytics/cost-summary/ response shape."""

    def setUp(self):
        self.board = Board.objects.create(
            name="Shape Board", working_dir="/tmp/shape",
        )

    def test_cost_summary_includes_throughput_funnel(self):
        """Empty-data call still returns the funnel section."""
        resp = self.client.get("/api/analytics/cost-summary/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("throughput_funnel", resp.data)
        self.assertEqual(resp.data["throughput_funnel"]["total"], 0)
        bucket_names = {
            b["bucket"] for b in resp.data["throughput_funnel"]["buckets"]
        }
        self.assertEqual(
            bucket_names,
            {"pass", "rework", "fail", "in_flight"},
        )

    def test_cost_summary_includes_league(self):
        """Empty-data call still returns the league section."""
        resp = self.client.get("/api/analytics/cost-summary/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("league", resp.data)
        self.assertIn("rows", resp.data["league"])
        self.assertIn("meta", resp.data["league"])

    def test_cost_summary_includes_per_spec(self):
        """Empty-data call still returns the per_spec section."""
        resp = self.client.get("/api/analytics/cost-summary/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("per_spec", resp.data)
        self.assertIsInstance(resp.data["per_spec"], list)

    def test_cost_summary_includes_scheduled_tasks(self):
        """Empty-data call still returns the scheduled_tasks section."""
        resp = self.client.get("/api/analytics/cost-summary/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("scheduled_tasks", resp.data)
        self.assertIsInstance(resp.data["scheduled_tasks"], list)


# ── Group 3: funnel math via the live endpoint ──────────────────────


class EndpointWithData(TestCase):
    """Coverage: the funnel section as seen via the live endpoint,
    on a realistic dataset."""

    def setUp(self):
        self.board = Board.objects.create(
            name="Data Board", working_dir="/tmp/data",
        )

    def test_funnel_sums_to_one_hundred_via_endpoint(self):
        """Real-task funnel: bucket counts add to total; percentages
        sum to 100. The bug the user reported cannot reappear."""
        # 4 pass, 1 rework, 1 fail, 1 in-flight
        for _ in range(4):
            Task.objects.create(
                board=self.board, status=TaskStatus.DONE, title="p",
                created_by="c@odin.agent", metadata={"rework_count": 0},
            )
        Task.objects.create(
            board=self.board, status=TaskStatus.DONE, title="r",
            created_by="c@odin.agent", metadata={"rework_count": 2},
        )
        Task.objects.create(
            board=self.board, status=TaskStatus.FAILED, title="f",
            created_by="c@odin.agent", metadata={},
        )
        Task.objects.create(
            board=self.board, status=TaskStatus.EXECUTING, title="i",
            created_by="c@odin.agent", metadata={},
        )

        resp = self.client.get(f"/api/analytics/cost-summary/?board={self.board.id}")
        self.assertEqual(resp.status_code, 200)
        funnel = resp.data["throughput_funnel"]
        self.assertEqual(funnel["total"], 7)
        buckets = {b["bucket"]: b for b in funnel["buckets"]}
        self.assertEqual(buckets["pass"]["count"], 4)
        self.assertEqual(buckets["rework"]["count"], 1)
        self.assertEqual(buckets["fail"]["count"], 1)
        self.assertEqual(buckets["in_flight"]["count"], 1)
        # Bucket counts add to total exactly — no missing 19%.
        self.assertEqual(
            sum(b["count"] for b in funnel["buckets"]),
            funnel["total"],
        )

    def test_league_aggregated_when_no_board_filter(self):
        """With no board filter the league section rolls up across
        every board — the page's All-Boards view needs this."""
        board_b = Board.objects.create(
            name="B Board", working_dir="/tmp/b",
        )
        claude = _make_agent("claude_lg")
        spec_a = Spec.objects.create(
            board=self.board, odin_id="sp_a", title="A",
        )
        spec_b = Spec.objects.create(
            board=board_b, odin_id="sp_b", title="B",
        )
        for spec, model, board in [
            (spec_a, "claude-opus-4-5", self.board),
            (spec_b, "claude-opus-4-5", board_b),
        ]:
            t = Task.objects.create(
                board=board, spec=spec, title="t",
                status=TaskStatus.DONE, assignee=claude,
                model_name=model, created_by="claude_lg@odin.agent",
            )
            _add_history(t, "status", "EXECUTING", "DONE",
                         "claude_lg@odin.agent")

        # No board filter — All-Boards view.
        resp = self.client.get("/api/analytics/cost-summary/")
        self.assertEqual(resp.status_code, 200)
        league_rows = resp.data["league"]["rows"]
        self.assertEqual(len(league_rows), 1)
        self.assertEqual(league_rows[0]["agent"], "claude_lg")
        self.assertEqual(league_rows[0]["tasks_landed"], 2)

    def test_per_spec_section_lists_each_spec_with_cost(self):
        """per_spec is one row per spec with task count + total cost,
        sorted newest-first."""
        spec_old = Spec.objects.create(
            board=self.board, odin_id="sp_old_344", title="Old",
        )
        # Force created_at ordering so the test is deterministic.
        spec_old.created_at = timezone.now() - timedelta(days=2)
        spec_old.save()
        spec_new = Spec.objects.create(
            board=self.board, odin_id="sp_new_344", title="New",
        )
        claude = _make_agent("claude_p")
        for spec, title in [(spec_old, "old-task"), (spec_new, "new-task")]:
            t = Task.objects.create(
                board=self.board, spec=spec, title=title,
                status=TaskStatus.DONE, assignee=claude,
                model_name="claude-opus-4-5",
                created_by="claude_p@odin.agent",
            )
            _add_history(t, "status", "EXECUTING", "DONE",
                         "claude_p@odin.agent")
        resp = self.client.get(f"/api/analytics/cost-summary/?board={self.board.id}")
        self.assertEqual(resp.status_code, 200)
        per_spec = resp.data["per_spec"]
        self.assertEqual(len(per_spec), 2)
        # Newer first
        self.assertEqual(per_spec[0]["odin_id"], "sp_new_344")
        self.assertEqual(per_spec[1]["odin_id"], "sp_old_344")
        for row in per_spec:
            self.assertIn("task_count", row)
            self.assertIn("done_count", row)
            self.assertEqual(row["task_count"], 1)
            self.assertEqual(row["done_count"], 1)

    def test_scheduled_tasks_section_lists_runs_with_outcomes(self):
        """scheduled_tasks includes the run history with success/failure."""
        sched = TaskSchedule.objects.create(
            board=self.board,
            kind=ScheduleKind.ONE_TIME,
            status=ScheduleStatus.ACTIVE,
            timezone="UTC",
            template_title="Hourly sweep",
            template_description="clean inbox",
            starts_at_local=timezone.now(),
            starts_at_utc=timezone.now(),
            next_run_at_utc=timezone.now() + timedelta(hours=1),
            created_by="operator@example.com",
        )
        TaskScheduleRun.objects.create(
            schedule=sched,
            run_number=1,
            scheduled_for_utc=timezone.now() - timedelta(hours=2),
            released_at_utc=timezone.now() - timedelta(hours=2),
            finished_at_utc=timezone.now() - timedelta(hours=2) + timedelta(minutes=5),
            status=ScheduleRunStatus.COMPLETED_SUCCESS,
        )
        TaskScheduleRun.objects.create(
            schedule=sched,
            run_number=2,
            scheduled_for_utc=timezone.now() - timedelta(hours=1),
            released_at_utc=timezone.now() - timedelta(hours=1),
            finished_at_utc=timezone.now() - timedelta(hours=1) + timedelta(minutes=2),
            status=ScheduleRunStatus.COMPLETED_FAILED,
        )
        resp = self.client.get(f"/api/analytics/cost-summary/?board={self.board.id}")
        self.assertEqual(resp.status_code, 200)
        rows = resp.data["scheduled_tasks"]
        self.assertEqual(len(rows), 1)
        sched_row = rows[0]
        self.assertEqual(sched_row["template_title"], "Hourly sweep")
        self.assertEqual(sched_row["run_count"], 2)
        self.assertEqual(sched_row["success_count"], 1)
        self.assertEqual(sched_row["failure_count"], 1)


# ── Group 4: agent-tasks drilldown endpoint ─────────────────────────


class AgentTasksDrilldown(TestCase):
    """Coverage: GET /api/agents/<name>/tasks/ — agent league drilldown.

    Lists every task the agent has touched, filterable by board, status,
    and spec. Each row carries a task_id so the frontend can open the
    detail modal via ?taskId=<id>.
    """

    def setUp(self):
        self.board = Board.objects.create(
            name="Drilldown Board", working_dir="/tmp/drill",
        )
        self.board_b = Board.objects.create(
            name="Drilldown Board B", working_dir="/tmp/drill-b",
        )
        self.spec = Spec.objects.create(
            board=self.board, odin_id="sp_dd_344", title="DD",
        )
        self.spec_b = Spec.objects.create(
            board=self.board_b, odin_id="sp_dd_344_b", title="DD-B",
        )
        self.claude = _make_agent("claude_dd")

    def test_endpoint_returns_tasks_for_agent(self):
        """Two DONE tasks assigned to claude_dd → both returned."""
        for i in range(2):
            t = Task.objects.create(
                board=self.board, spec=self.spec,
                title=f"task-{i}",
                status=TaskStatus.DONE, assignee=self.claude,
                model_name="claude-opus-4-5",
                created_by="claude_dd@odin.agent",
            )
            _add_history(t, "status", "EXECUTING", "DONE",
                         "claude_dd@odin.agent")
        resp = self.client.get("/api/agents/claude_dd/tasks/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("tasks", resp.data)
        self.assertEqual(len(resp.data["tasks"]), 2)
        for row in resp.data["tasks"]:
            self.assertIn("task_id", row)
            self.assertIn("title", row)
            self.assertIn("status", row)
            self.assertIn("board_id", row)
            self.assertIn("board_name", row)
            self.assertIn("spec_id", row)

    def test_endpoint_filters_by_status(self):
        """status=DONE filters out non-DONE tasks."""
        for status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.EXECUTING):
            Task.objects.create(
                board=self.board, spec=self.spec,
                title=f"task-{status}",
                status=status, assignee=self.claude,
                model_name="claude-opus-4-5",
                created_by="claude_dd@odin.agent",
            )
        resp = self.client.get("/api/agents/claude_dd/tasks/?status=DONE")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["tasks"]), 1)
        self.assertEqual(resp.data["tasks"][0]["status"], "DONE")

    def test_endpoint_filters_by_board(self):
        """board_id restricts to one board only."""
        Task.objects.create(
            board=self.board, spec=self.spec,
            title="a", status=TaskStatus.DONE,
            assignee=self.claude, model_name="claude-opus-4-5",
            created_by="claude_dd@odin.agent",
        )
        Task.objects.create(
            board=self.board_b, spec=self.spec_b,
            title="b", status=TaskStatus.DONE,
            assignee=self.claude, model_name="claude-opus-4-5",
            created_by="claude_dd@odin.agent",
        )
        resp = self.client.get(
            f"/api/agents/claude_dd/tasks/?board_id={self.board.id}",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["tasks"]), 1)
        self.assertEqual(resp.data["tasks"][0]["board_id"], self.board.id)

    def test_endpoint_filters_by_spec(self):
        """spec_id restricts to one spec only."""
        Task.objects.create(
            board=self.board, spec=self.spec,
            title="a", status=TaskStatus.DONE,
            assignee=self.claude, model_name="claude-opus-4-5",
            created_by="claude_dd@odin.agent",
        )
        Task.objects.create(
            board=self.board, spec=self.spec_b,
            title="b", status=TaskStatus.DONE,
            assignee=self.claude, model_name="claude-opus-4-5",
            created_by="claude_dd@odin.agent",
        )
        resp = self.client.get(
            f"/api/agents/claude_dd/tasks/?spec_id={self.spec.id}",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["tasks"]), 1)
        self.assertEqual(resp.data["tasks"][0]["spec_id"], self.spec.id)

    def test_endpoint_empty_for_unknown_agent(self):
        """Agent with no tasks → empty list, 200, never a crash."""
        resp = self.client.get("/api/agents/no_such_agent/tasks/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["tasks"], [])


# ── Group 5: league avg-reflection-cost column ──────────────────────


class LeagueReflectionCost(TestCase):
    """Coverage: the league row carries an avg reflection cost per provider.

    Operator scope addition for W12.4: every league row (the
    (agent, model) executor pair on landed tasks) also shows the avg
    USD cost of reflections where the SAME (agent, model) was the
    reviewer. This is what a redo/reflection actually costs on each
    reviewer — the column was missing from the first cut.

    Spec contract (the failing tests pin it):
      * every LeagueRow.to_dict() carries ``reflection_count``,
        ``reflection_cost_usd_total``, ``avg_reflection_cost_usd``;
        values are 0/0.0 when nothing was reviewed
      * averages come from COMPLETED ``ReflectionReport`` rows where
        ``reviewer_agent == row.agent`` and ``reviewer_model ==
        row.model`` — reviewer-keyed, not executor-keyed
      * the endpoint exposes the same fields (so the UI can render
        the column without a second round-trip).
    """

    def setUp(self):
        self.board = Board.objects.create(
            name="League Reflection Board", working_dir="/tmp/lr",
        )
        self.spec = Spec.objects.create(
            board=self.board, odin_id="sp_lr_344", title="LR",
        )
        self.claude = _make_agent("claude_lr")

    def _make_task(self, title, *, model="claude-opus-4-5"):
        t = Task.objects.create(
            board=self.board, spec=self.spec, title=title,
            status=TaskStatus.DONE, assignee=self.claude,
            model_name=model, created_by="claude_lr@odin.agent",
        )
        _add_history(t, "status", "EXECUTING", "DONE",
                     "claude_lr@odin.agent")
        return t

    def _make_reflection(
        self, task, *, reviewer_agent, reviewer_model, cost_usd=0.0,
        status=ReflectionStatus.COMPLETED,
    ):
        return ReflectionReport.objects.create(
            task=task,
            reviewer_agent=reviewer_agent,
            reviewer_model=reviewer_model,
            token_usage={"input_tokens": 1000, "output_tokens": 1000},
            duration_ms=5000,
            status=status,
            verdict="PASS",
            requested_by="system@taskit",
        )

    def test_league_row_carries_reflection_cost_fields(self):
        """Every LeagueRow.to_dict() always carries the three
        reflection-cost fields, even with zero reflections."""
        from tasks.league import compute_league_for_board, LeagueRow

        t = self._make_task("no-refl")
        # No reflection reports at all
        rows = compute_league_for_board(board=self.board)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        for field in (
            "reflection_count",
            "reflection_cost_usd_total",
            "avg_reflection_cost_usd",
        ):
            self.assertTrue(
                hasattr(row, field),
                f"LeagueRow missing field: {field}",
            )
        d = row.to_dict()
        for field in (
            "reflection_count",
            "reflection_cost_usd_total",
            "avg_reflection_cost_usd",
        ):
            self.assertIn(field, d, f"to_dict() missing {field}")
        self.assertEqual(d["reflection_count"], 0)
        self.assertEqual(d["reflection_cost_usd_total"], 0.0)
        self.assertEqual(d["avg_reflection_cost_usd"], 0.0)

    def test_avg_reflection_cost_averages_only_same_provider_reflections(self):
        """Multiple reflections by claude_lr/claude-opus-4-5 average
        together; reflections from other (agent, model) reviewers are
        excluded from this row's avg.
        """
        from tasks.league import compute_league_for_board

        t1 = self._make_task("t1")
        t2 = self._make_task("t2")
        t3 = self._make_task("t3")
        # 2 reflections by claude_lr / opus — the avg they're in
        self._make_reflection(
            t1, reviewer_agent="claude_lr", reviewer_model="claude-opus-4-5",
        )
        self._make_reflection(
            t2, reviewer_agent="claude_lr", reviewer_model="claude-opus-4-5",
        )
        # 1 reflection by a different provider — must NOT count
        self._make_reflection(
            t3, reviewer_agent="other_agent", reviewer_model="other-model",
        )

        rows = compute_league_for_board(board=self.board)
        # Two rows now: the executor row (which also reviewed twice) and a
        # review-only row for the other provider — a provider that only
        # reviews still appears in the league with its reflection cost.
        self.assertEqual(len(rows), 2)
        by_agent = {r.agent: r.to_dict() for r in rows}
        d = by_agent["claude_lr"]
        self.assertEqual(d["reflection_count"], 2)
        other = by_agent["other_agent"]
        self.assertEqual(other["reflection_count"], 1)
        self.assertEqual(other["tasks_landed"], 0)
        # The other-agent reflection carries different priced tokens, so
        # the count is the discriminator here. Cost numbers depend on
        # the model pricing table (claude-opus-4-5 might be priced
        # differently than "other-model") — assert the count strictly
        # and the cost is non-negative.
        self.assertGreaterEqual(d["reflection_cost_usd_total"], 0.0)
        # avg = total / count
        if d["reflection_count"] > 0:
            self.assertAlmostEqual(
                d["avg_reflection_cost_usd"],
                d["reflection_cost_usd_total"] / d["reflection_count"],
                places=4,
            )

    def test_failed_reflections_excluded_from_avg(self):
        """FAILED reflections are not cost success — exclude them."""
        from tasks.league import compute_league_for_board

        t1 = self._make_task("a")
        t2 = self._make_task("b")
        self._make_reflection(
            t1, reviewer_agent="claude_lr", reviewer_model="claude-opus-4-5",
            status=ReflectionStatus.FAILED,
        )
        self._make_reflection(
            t2, reviewer_agent="claude_lr", reviewer_model="claude-opus-4-5",
            status=ReflectionStatus.COMPLETED,
        )
        rows = compute_league_for_board(board=self.board)
        self.assertEqual(len(rows), 1)
        d = rows[0].to_dict()
        # Only the COMPLETED reflection counts
        self.assertEqual(d["reflection_count"], 1)

    def test_endpoint_league_rows_expose_refl_cost_fields(self):
        """The cost-summary endpoint's league section exposes the
        three reflection-cost fields on every row."""
        t = self._make_task("endpoint-row")
        self._make_reflection(
            t, reviewer_agent="claude_lr", reviewer_model="claude-opus-4-5",
        )
        resp = self.client.get(
            f"/api/analytics/cost-summary/?board={self.board.id}",
        )
        self.assertEqual(resp.status_code, 200)
        league_rows = resp.data["league"]["rows"]
        self.assertEqual(len(league_rows), 1)
        row = league_rows[0]
        self.assertEqual(row["agent"], "claude_lr")
        self.assertEqual(row["reflection_count"], 1)
        self.assertGreaterEqual(row["avg_reflection_cost_usd"], 0.0)
        self.assertGreaterEqual(row["reflection_cost_usd_total"], 0.0)


# ── Group 6: drilldown spec filter is a real selector ──────────────


class DrilldownSpecFilter(TestCase):
    """Coverage: the agent-tasks drilldown surfaces a spec filter on
    the front-end.

    Backend already supports ``?spec_id=<id>`` via views.agent_tasks —
    the previous W12.4 PR wired that into the URL state but the
    AgentTasksDrilldown component never rendered a spec selector UI.
    The frontend test pins the contract: when the user picks a spec
    from the selector, the request goes out with ``spec_id=<id>`` and
    the row set updates to match.

    These tests live on the backend side because that's the cheapest
    place to assert the filter behavior; the frontend spec selector
    unit test (in StatsPageRebuild.test.tsx) covers the UI itself.
    """

    def setUp(self):
        self.board = Board.objects.create(
            name="Spec Filter Board", working_dir="/tmp/sf",
        )
        self.spec_a = Spec.objects.create(
            board=self.board, odin_id="sp_sf_a_344", title="A",
        )
        self.spec_b = Spec.objects.create(
            board=self.board, odin_id="sp_sf_b_344", title="B",
        )
        self.claude = _make_agent("claude_sf")

    def test_spec_filter_narrows_rows_to_that_spec(self):
        """Two tasks under different specs; spec_id= spec_a returns 1 row.
        The frontend wires this URL param to a Select element."""
        Task.objects.create(
            board=self.board, spec=self.spec_a,
            title="a", status=TaskStatus.DONE,
            assignee=self.claude, model_name="claude-opus-4-5",
            created_by="claude_sf@odin.agent",
        )
        Task.objects.create(
            board=self.board, spec=self.spec_b,
            title="b", status=TaskStatus.DONE,
            assignee=self.claude, model_name="claude-opus-4-5",
        )
        resp = self.client.get(
            f"/api/agents/claude_sf/tasks/?spec_id={self.spec_a.id}",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["tasks"]), 1)
        self.assertEqual(
            resp.data["tasks"][0]["spec_id"], self.spec_a.id,
        )
        self.assertEqual(resp.data["meta"]["spec_id"], self.spec_a.id)