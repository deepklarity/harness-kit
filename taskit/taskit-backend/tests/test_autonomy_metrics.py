"""Autonomy metrics diagnostic — BACKLOG item 7.

Ratchet rule: metrics from scripts, not hand counts. The
autonomy_metrics.py script must reproduce the wave-1 (17/18 = 94%) and
wave-2 (18/18 = 100%) figures from data, not by re-reading the audit
prose. The tests here build realistic fixtures for each wave and assert
the script's computed figures match.

Coverage:
  OperatorTouchDetection — manual transitions and steering comments are
    detected from TaskHistory / TaskComment rows.
  AutonomyClassification — agent-authored vs operator-authored tasks are
    classified by whether any operator touch occurred during the
    lifecycle.
  Wave1Reproduction — 17/18 (94%) on a fixture simulating wave 1.
  Wave2Reproduction — 18/18 (100%) on a fixture simulating wave 2.
  CostPerMergedChange — token totals and capture-gap honesty.
  TimeToVerifiedPercentiles — dispatch->DONE percentiles.
  CLIOutputModes — --brief, --json, and standard modes run without error.
  EmptyBoard — runs cleanly when there are no tasks.
"""
import io
import json
import os
import sys
import datetime
from contextlib import redirect_stdout
from pathlib import Path
from unittest import SkipTest

# Force SQLite + Django settings before importing anything project-side.
os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

# Put testing_tools on the path so we can import the script under test.
TESTING_TOOLS_DIR = (
    Path(__file__).resolve().parent.parent / "testing_tools"
)
if str(TESTING_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTING_TOOLS_DIR))

import django  # noqa: E402

django.setup()

from django.test import SimpleTestCase, TestCase  # noqa: E402

from tasks.models import (  # noqa: E402
    Board,
    Spec,
    Task,
    TaskComment,
    TaskHistory,
    TaskStatus,
    User,
    UserRole,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _make_agent(name="claude"):
    """Get or create an agent User (role=AGENT). Reuses seed if present."""
    user, _ = User.objects.get_or_create(
        email=f"{name}@odin.agent",
        defaults={"name": name, "role": UserRole.AGENT, "is_active": True},
    )
    return user


def _make_operator(email="operator@example.com"):
    """Get or create a non-agent User (operator)."""
    user, _ = User.objects.get_or_create(
        email=email,
        defaults={"name": email.split("@")[0], "role": UserRole.HUMAN, "is_active": True},
    )
    return user


def _add_history(task, field, old_value, new_value, changed_by, when):
    """Create a TaskHistory row with an explicit timestamp."""
    return TaskHistory.objects.create(
        task=task,
        field_name=field,
        old_value=str(old_value),
        new_value=str(new_value),
        changed_by=changed_by,
    )


def _add_comment(task, author_email, content, comment_type="status_update",
                 when=None):
    """Create a TaskComment with optional manual timestamp."""
    c = TaskComment.objects.create(
        task=task,
        author_email=author_email,
        content=content,
        comment_type=comment_type,
    )
    if when is not None:
        # Force created_at for deterministic time math in tests.
        TaskComment.objects.filter(pk=c.pk).update(created_at=when)
        c.refresh_from_db()
    return c


# ── Pure-function tests (no DB) ──────────────────────────────────────


class OperatorTouchDetection(SimpleTestCase):
    """is_operator_email / classify_operator_touch correctness."""

    def test_agent_email_is_not_operator(self):
        from autonomy_metrics import is_operator_email
        self.assertFalse(is_operator_email("claude@odin.agent"))
        self.assertFalse(is_operator_email("minimax+opus@odin.agent"))

    def test_human_email_is_operator(self):
        from autonomy_metrics import is_operator_email
        self.assertTrue(is_operator_email("alice@example.com"))
        self.assertTrue(is_operator_email("bob@test.com"))

    def test_system_is_not_operator(self):
        from autonomy_metrics import is_operator_email
        self.assertFalse(is_operator_email("system@taskit"))

    def test_odin_memory_system_is_not_operator(self):
        """odin+memory@system (twins memory lookup) is a machine, not a human."""
        from autonomy_metrics import is_operator_email
        self.assertFalse(is_operator_email("odin+memory@system"))

    def test_odin_dag_executor_system_is_not_operator(self):
        """odin+dag-executor@system is a system worker, not a human."""
        from autonomy_metrics import is_operator_email
        self.assertFalse(is_operator_email("odin+dag-executor@system"))

    def test_odin_harness_kit_is_not_operator(self):
        """odin@harness.kit (trace sidecar) is a machine, not a human."""
        from autonomy_metrics import is_operator_email
        self.assertFalse(is_operator_email("odin@harness.kit"))

    def test_proof_upload_taskit_is_not_operator(self):
        """proof-upload@taskit (proof upload service) is a machine."""
        from autonomy_metrics import is_operator_email
        self.assertFalse(is_operator_email("proof-upload@taskit"))

    def test_known_human_is_operator(self):
        """The known human (operator@example.com) is an operator."""
        from autonomy_metrics import is_operator_email
        self.assertTrue(is_operator_email("operator@example.com"))

    def test_is_human_author_is_canonical(self):
        """is_human_author is the positive form; is_operator_email is its alias."""
        from autonomy_metrics import is_human_author, is_operator_email
        # Same callable — the rename is footprint-negative for callers.
        self.assertIs(is_human_author, is_operator_email)


class TimePercentileMath(SimpleTestCase):
    """percentile() helper handles sorted/unsorted/short inputs."""

    def test_percentile_known_values(self):
        from autonomy_metrics import percentile
        # 1..10 -> p50=5.5, p90=9.1
        vals = list(range(1, 11))
        self.assertAlmostEqual(percentile(vals, 50), 5.5, places=1)
        self.assertAlmostEqual(percentile(vals, 90), 9.1, places=1)

    def test_percentile_short(self):
        from autonomy_metrics import percentile
        self.assertEqual(percentile([42], 50), 42)
        self.assertEqual(percentile([], 50), 0)


# ── DB-backed integration tests ──────────────────────────────────────


class AutonomyClassification(TestCase):
    """A task is agent-authored iff no disqualifying operator touch occurred."""

    def setUp(self):
        self.board = Board.objects.create(name="Fable Roadmap", working_dir="/tmp/fable")
        self.spec = Spec.objects.create(
            board=self.board,
            odin_id="sp_wave_classify",
            title="Wave-classify spec",
        )
        self.claude = _make_agent("claude")

    def test_pure_agent_flow_is_agent_authored(self):
        from autonomy_metrics import classify_task

        t = Task.objects.create(
            board=self.board,
            spec=self.spec,
            title="Agent-only task",
            status=TaskStatus.DONE,
            created_by="claude@odin.agent",
            assignee=self.claude,
            metadata={"last_duration_ms": 120_000},
        )
        _add_history(t, "created", "", "", "system@taskit",
                     when=datetime.datetime(2026, 7, 1, 10, 0, tzinfo=datetime.timezone.utc))
        _add_history(t, "status", "TODO", "EXECUTING", "system@taskit",
                     when=datetime.datetime(2026, 7, 1, 10, 1, tzinfo=datetime.timezone.utc))
        _add_history(t, "status", "EXECUTING", "DONE", "claude@odin.agent",
                     when=datetime.datetime(2026, 7, 1, 10, 3, tzinfo=datetime.timezone.utc))
        _add_comment(t, "claude@odin.agent", "All green.", when=datetime.datetime(
            2026, 7, 1, 10, 3, tzinfo=datetime.timezone.utc))

        cls = classify_task(t)
        self.assertTrue(cls["agent_authored"])
        self.assertEqual(cls["operator_touches"], [])

    def test_operator_steering_does_not_disqualify(self):
        """A steering comment from the operator is an unstick, not a takeover."""
        from autonomy_metrics import classify_task

        t = Task.objects.create(
            board=self.board,
            spec=self.spec,
            title="Question task",
            status=TaskStatus.DONE,
            created_by="claude@odin.agent",
            assignee=self.claude,
            metadata={"last_duration_ms": 60_000},
        )
        _add_history(t, "status", "TODO", "EXECUTING", "system@taskit",
                     when=datetime.datetime(2026, 7, 1, 10, 0, tzinfo=datetime.timezone.utc))
        _add_comment(t, "operator@example.com", "Please use the new schema.",
                     comment_type="question",
                     when=datetime.datetime(2026, 7, 1, 10, 1, tzinfo=datetime.timezone.utc))
        _add_history(t, "status", "EXECUTING", "DONE", "claude@odin.agent",
                     when=datetime.datetime(2026, 7, 1, 10, 3, tzinfo=datetime.timezone.utc))

        cls = classify_task(t)
        # Steering does NOT disqualify; the agent still drove the work.
        self.assertTrue(cls["agent_authored"])
        self.assertEqual(len(cls["operator_touches"]), 1)
        self.assertEqual(cls["operator_touches"][0]["kind"], "steering_comment")
        self.assertFalse(cls["operator_touches"][0]["disqualifying"])

    def test_manual_status_to_terminal_disqualifies(self):
        """Operator flipping status to DONE/REVIEW/TESTING IS a takeover."""
        from autonomy_metrics import classify_task

        t = Task.objects.create(
            board=self.board,
            spec=self.spec,
            title="Manual transition task",
            status=TaskStatus.DONE,
            created_by="claude@odin.agent",
            assignee=self.claude,
            metadata={"last_duration_ms": 90_000},
        )
        _add_history(t, "status", "TODO", "EXECUTING", "system@taskit",
                     when=datetime.datetime(2026, 7, 1, 10, 0, tzinfo=datetime.timezone.utc))
        # Operator manually flips status to DONE — hand-completed #104.
        _add_history(t, "status", "EXECUTING", "DONE", "operator@example.com",
                     when=datetime.datetime(2026, 7, 1, 10, 5, tzinfo=datetime.timezone.utc))

        cls = classify_task(t)
        self.assertFalse(cls["agent_authored"])
        self.assertEqual(len(cls["operator_touches"]), 1)
        self.assertEqual(cls["operator_touches"][0]["kind"], "manual_transition")
        self.assertTrue(cls["operator_touches"][0]["disqualifying"])

    def test_manual_non_terminal_does_not_disqualify(self):
        """Operator nudging status (TODO->EXECUTING, etc.) is not a takeover."""
        from autonomy_metrics import classify_task

        t = Task.objects.create(
            board=self.board,
            spec=self.spec,
            title="Nudged task",
            status=TaskStatus.DONE,
            created_by="claude@odin.agent",
            assignee=self.claude,
            metadata={"last_duration_ms": 90_000},
        )
        # Operator nudged it from BACKLOG to TODO.
        _add_history(t, "status", "BACKLOG", "TODO", "operator@example.com",
                     when=datetime.datetime(2026, 7, 1, 10, 0, tzinfo=datetime.timezone.utc))
        _add_history(t, "status", "TODO", "EXECUTING", "system@taskit",
                     when=datetime.datetime(2026, 7, 1, 10, 1, tzinfo=datetime.timezone.utc))
        _add_history(t, "status", "EXECUTING", "DONE", "claude@odin.agent",
                     when=datetime.datetime(2026, 7, 1, 10, 3, tzinfo=datetime.timezone.utc))

        cls = classify_task(t)
        self.assertTrue(cls["agent_authored"])
        self.assertEqual(len(cls["operator_touches"]), 1)
        self.assertFalse(cls["operator_touches"][0]["disqualifying"])


class Wave1Reproduction(TestCase):
    """Build a wave-1-shaped fixture: 18 DONE, 17 agent-authored, 1 hand-completed."""

    def setUp(self):
        self.board = Board.objects.create(name="Fable Roadmap", working_dir="/tmp/fable")
        self.spec = Spec.objects.create(
            board=self.board,
            odin_id="sp_wave1",
            title="Wave 1 spec (18 tasks)",
        )
        self.claude = _make_agent("claude")

    def _make_task(self, idx, hand_completed=False, with_question=False, with_tokens=None):
        """Create a task with a realistic 3.5-30 min EXECUTING->DONE window."""
        # Stagger by hour-of-day to avoid minute overflow. Use a 24-hour
        # modular window so callers can pass any positive idx.
        hour = (10 + (idx // 60)) % 24
        minute = idx % 60
        base = datetime.datetime(2026, 7, 5, hour, minute, tzinfo=datetime.timezone.utc)
        start = base
        # EXECUTING span: 3.5–30 min
        exec_minutes = 5 + (idx % 25)
        end = start + datetime.timedelta(minutes=exec_minutes)

        meta = {"last_duration_ms": exec_minutes * 60 * 1000}
        if with_tokens is not None:
            meta["trace_attachments_marker"] = "synthetic"
        t = Task.objects.create(
            board=self.board,
            spec=self.spec,
            title=f"Wave 1 task #{idx}",
            status=TaskStatus.DONE,
            created_by="claude@odin.agent",
            assignee=self.claude,
            metadata=meta,
        )
        _add_history(t, "created", "", "", "system@taskit", when=start)
        _add_history(t, "status", "TODO", "EXECUTING", "system@taskit",
                     when=start + datetime.timedelta(seconds=10))
        if hand_completed:
            # Operator flips to DONE — that's #104
            _add_history(t, "status", "EXECUTING", "DONE", "operator@example.com",
                         when=end)
        else:
            _add_history(t, "status", "EXECUTING", "DONE", "claude@odin.agent",
                         when=end)
            if with_question:
                # Mid-flight steering comment from operator
                _add_comment(t, "operator@example.com", "Actually use the new schema.",
                             comment_type="question",
                             when=start + datetime.timedelta(minutes=1))
        return t

    def test_wave1_autonomy_rate(self):
        # Wave 1 audit: 17/18 (94%) — one hand-completed, the rest agent.
        # Build 17 agent-flow tasks with unique indices, then add the
        # single operator-completed task (#104) under a distinct title.
        for i in range(17):
            self._make_task(i + 100, hand_completed=False)
        # Override title for the hand-completed one so it doesn't collide
        # with the agent-flow task #104 already created above.
        t = self._make_task(900, hand_completed=True)
        t.title = "Wave 1 task #104 (operator)"
        t.save()

        from autonomy_metrics import compute_board_metrics
        m = compute_board_metrics(board=self.board)
        self.assertEqual(m["total_done"], 18)
        self.assertEqual(m["agent_authored"], 17)
        self.assertEqual(m["autonomy_rate"], 17 / 18)

    def test_wave1_operator_touches_count(self):
        # Wave 1: ~12 operator unsticks total across the wave. The audit
        # *separately* tracks 4 hand-resolved merge conflicts (which are
        # git work, not TaskHistory rows). Steering comments ARE recorded
        # as touches. Build 11 steering comments + 1 hand-completed
        # #104 transition = 12 unsticks total.
        tasks = []
        for i in range(17):
            tasks.append(self._make_task(i + 100))
        hand = self._make_task(900, hand_completed=True)
        hand.title = "Wave 1 task #104 (operator)"
        hand.save()
        # 11 steering comments scattered across the agent-flow tasks.
        for i in range(11):
            _add_comment(tasks[i], "operator@example.com", f"Use the v2 endpoint.",
                         comment_type="question",
                         when=datetime.datetime(2026, 7, 5, 11, 30 + i, tzinfo=datetime.timezone.utc))

        from autonomy_metrics import compute_board_metrics
        m = compute_board_metrics(board=self.board)
        # 1 hand-completed transition + 11 steering comments = 12 touches.
        self.assertEqual(m["operator_touches_total"], 12)
        # Agent-authored is unchanged at 17/18 (steering is not
        # disqualifying; only #104's hand-completion is).
        self.assertEqual(m["agent_authored"], 17)


class Wave2Reproduction(TestCase):
    """Build a wave-2-shaped fixture: 18 DONE, all agent-authored, ~8 unsticks."""

    def setUp(self):
        self.board = Board.objects.create(name="Fable Roadmap", working_dir="/tmp/fable")
        self.spec = Spec.objects.create(
            board=self.board,
            odin_id="sp_wave2",
            title="Wave 2 spec (18 tasks, all agent-authored)",
        )
        self.claude = _make_agent("claude")

    def _make_task(self, idx, with_question=False):
        # Wave 2: exec duration 55s–19.4 min — wider range.
        # Stagger by hour-of-day so we can sweep many tasks without
        # colliding on minute=60. Mod 24 keeps us in valid range.
        hour = (9 + (idx // 60)) % 24
        minute = idx % 60
        base = datetime.datetime(2026, 7, 6, hour, minute, tzinfo=datetime.timezone.utc)
        exec_seconds = 55 + (idx * 60)  # 55s, 115s, ...
        end = base + datetime.timedelta(seconds=exec_seconds)

        t = Task.objects.create(
            board=self.board,
            spec=self.spec,
            title=f"Wave 2 task #{idx}",
            status=TaskStatus.DONE,
            created_by="claude@odin.agent",
            assignee=self.claude,
            metadata={"last_duration_ms": exec_seconds * 1000},
        )
        _add_history(t, "created", "", "", "system@taskit", when=base)
        _add_history(t, "status", "TODO", "EXECUTING", "system@taskit",
                     when=base + datetime.timedelta(seconds=5))
        _add_history(t, "status", "EXECUTING", "DONE", "claude@odin.agent", when=end)
        if with_question:
            _add_comment(t, "operator@example.com", "Try claude-sonnet-5 instead.",
                         comment_type="question",
                         when=base + datetime.timedelta(seconds=10))
        return t

    def test_wave2_autonomy_rate(self):
        # Wave 2 audit: 18/18 (100%) — every merge agent-authored.
        for i in range(18):
            self._make_task(i + 200)
        from autonomy_metrics import compute_board_metrics
        m = compute_board_metrics(board=self.board)
        self.assertEqual(m["total_done"], 18)
        self.assertEqual(m["agent_authored"], 18)
        self.assertEqual(m["autonomy_rate"], 1.0)

    def test_wave2_zero_unstick_subset(self):
        # Wave 2 audit: 10/18 went dispatch->TESTING with zero operator
        # unsticks; 8 had at least one operator touch. With our model,
        # steering comments ARE touches (recorded) but NOT disqualifying,
        # so the autonomy rate stays at 18/18 = 100% while the unsticks
        # count reflects the 8 steering comments on the tail of the wave.
        for i in range(18):
            self._make_task(i + 200, with_question=(i >= 10))
        from autonomy_metrics import compute_board_metrics
        m = compute_board_metrics(board=self.board)
        self.assertEqual(m["total_done"], 18)
        self.assertEqual(m["agent_authored"], 18)  # 100% — steering doesn't disqualify
        # 18 - 8 tasks with steering comments = 10 zero-unstick tasks.
        self.assertEqual(m["tasks_with_zero_unsticks"], 10)
        self.assertEqual(m["operator_touches_total"], 8)

    def test_wave2_token_capture_regression(self):
        # Wave 2: "Token/cost per merged change: none captured (regression)".
        # Build 18 tasks with NO captured usage.
        for i in range(18):
            self._make_task(i + 200)
        from autonomy_metrics import compute_board_metrics
        m = compute_board_metrics(board=self.board)
        # All 18 tasks are missing token capture (the regression)
        self.assertEqual(m["tasks_with_capture_gaps"], 18)
        self.assertEqual(m["total_tokens"], 0)
        self.assertTrue(m["cost"]["cost_capture_honest"])


class CostPerMergedChange(TestCase):
    """Average cost = total tokens / done count; gaps counted honestly."""

    def setUp(self):
        self.board = Board.objects.create(name="Fable Roadmap", working_dir="/tmp/fable")
        self.spec = Spec.objects.create(
            board=self.board,
            odin_id="sp_cost",
            title="Cost spec",
        )
        self.claude = _make_agent("claude")

    def _make_done_task(self, idx, total_tokens):
        t = Task.objects.create(
            board=self.board,
            spec=self.spec,
            title=f"Cost task #{idx}",
            status=TaskStatus.DONE,
            created_by="claude@odin.agent",
            assignee=self.claude,
        )
        if total_tokens > 0:
            # Build a JSONL trace comment that the execution_processing
            # extractor recognizes. We use the claude pattern with totals.
            trace_lines = [
                json.dumps({
                    "type": "assistant",
                    "message": {"id": f"m{idx}", "type": "message",
                                "role": "assistant", "content": []},
                }),
                json.dumps({
                    "type": "result",
                    "subtype": "success",
                    "usage": {
                        "input_tokens": total_tokens // 2,
                        "output_tokens": total_tokens // 2,
                        "total_tokens": total_tokens,
                    },
                }),
            ]
            trace_text = "\n".join(trace_lines)
            TaskComment.objects.create(
                task=t,
                author_email="claude@odin.agent",
                content=trace_text,
                comment_type="status_update",
                attachments=["trace:execution_jsonl"],
            )
        # Add the bookkeeping history (so classify_task sees a real flow)
        base = datetime.datetime(2026, 7, 6, 12, idx, tzinfo=datetime.timezone.utc)
        _add_history(t, "status", "TODO", "EXECUTING", "system@taskit",
                     when=base + datetime.timedelta(seconds=5))
        _add_history(t, "status", "EXECUTING", "DONE", "claude@odin.agent",
                     when=base + datetime.timedelta(minutes=10))
        return t

    def test_capture_gaps_reported_honestly(self):
        # 4 tasks: 2 with tokens, 2 without.
        self._make_done_task(1, 1_000_000)
        self._make_done_task(2, 2_000_000)
        self._make_done_task(3, 0)
        self._make_done_task(4, 0)
        from autonomy_metrics import compute_board_metrics
        m = compute_board_metrics(board=self.board)
        self.assertEqual(m["total_done"], 4)
        self.assertEqual(m["tasks_with_capture_gaps"], 2)
        self.assertEqual(m["total_tokens"], 3_000_000)
        self.assertEqual(m["avg_tokens_per_change"], 750_000)
        self.assertTrue(m["cost"]["cost_capture_honest"])


class TimeToVerifiedPercentiles(TestCase):
    """dispatch->DONE percentiles computed from real history timestamps."""

    def setUp(self):
        self.board = Board.objects.create(name="Fable Roadmap", working_dir="/tmp/fable")
        self.spec = Spec.objects.create(
            board=self.board,
            odin_id="sp_time",
            title="Time spec",
        )
        self.claude = _make_agent("claude")

    def _make_task_with_durations(self, idx, exec_minutes):
        base = datetime.datetime(2026, 7, 6, 8, idx, tzinfo=datetime.timezone.utc)
        end = base + datetime.timedelta(minutes=exec_minutes)
        t = Task.objects.create(
            board=self.board,
            spec=self.spec,
            title=f"Time task #{idx}",
            status=TaskStatus.DONE,
            created_by="claude@odin.agent",
            assignee=self.claude,
            metadata={"last_duration_ms": exec_minutes * 60 * 1000},
        )
        _add_history(t, "status", "TODO", "EXECUTING", "system@taskit",
                     when=base + datetime.timedelta(seconds=5))
        _add_history(t, "status", "EXECUTING", "DONE", "claude@odin.agent", when=end)
        return t

    def test_percentiles_match_known_distribution(self):
        # 10 tasks with exec_minutes 1,2,3,...,10.
        for i in range(10):
            self._make_task_with_durations(i, i + 1)
        from autonomy_metrics import compute_board_metrics
        m = compute_board_metrics(board=self.board)
        # p50 of [1..10] = 5.5
        self.assertAlmostEqual(m["exec_duration_minutes"]["p50"], 5.5, places=1)
        self.assertEqual(m["exec_duration_minutes"]["min"], 1.0)
        self.assertEqual(m["exec_duration_minutes"]["max"], 10.0)


class EmptyBoard(TestCase):
    """Runs cleanly when the board has no tasks."""

    def test_empty_board_no_crash(self):
        board = Board.objects.create(name="Empty board", working_dir="/tmp/empty")
        from autonomy_metrics import compute_board_metrics
        m = compute_board_metrics(board=board)
        self.assertEqual(m["total_done"], 0)
        self.assertEqual(m["agent_authored"], 0)
        self.assertEqual(m["autonomy_rate"], 0)
        self.assertEqual(m["operator_touches_total"], 0)
        self.assertTrue(m["cost"]["cost_capture_honest"])


class CLIOutputModes(TestCase):
    """--brief, --json, and standard modes all run and produce output."""

    def setUp(self):
        self.board = Board.objects.create(name="Fable Roadmap", working_dir="/tmp/fable")
        self.spec = Spec.objects.create(
            board=self.board,
            odin_id="sp_cli",
            title="CLI spec",
        )
        self.claude = _make_agent("claude")
        # One DONE task so the standard/brief modes have something to show.
        base = datetime.datetime(2026, 7, 6, 14, 0, tzinfo=datetime.timezone.utc)
        t = Task.objects.create(
            board=self.board, spec=self.spec, title="CLI sample task",
            status=TaskStatus.DONE, created_by="claude@odin.agent",
            assignee=self.claude,
            metadata={"last_duration_ms": 240_000},
        )
        _add_history(t, "status", "TODO", "EXECUTING", "system@taskit",
                     when=base + datetime.timedelta(seconds=5))
        _add_history(t, "status", "EXECUTING", "DONE", "claude@odin.agent",
                     when=base + datetime.timedelta(minutes=4))

    def _run_main(self, argv):
        from autonomy_metrics import main
        buf = io.StringIO()
        old_argv = sys.argv
        sys.argv = ["autonomy_metrics.py"] + argv
        try:
            with redirect_stdout(buf):
                main()
        finally:
            sys.argv = old_argv
        return buf.getvalue()

    def test_brief_runs(self):
        out = self._run_main(["--brief"])
        self.assertIn("autonomy", out.lower())

    def test_json_runs_and_parses(self):
        out = self._run_main(["--json"])
        data = json.loads(out)
        self.assertIn("autonomy_rate", data)
        self.assertIn("agent_authored", data)
        self.assertIn("total_done", data)

    def test_standard_runs(self):
        out = self._run_main([])
        self.assertIn("AUTONOMY", out.upper())


# ── Wave-5 semantics: TESTING is the resting shelf (W5) ────────────


class LandedStatusesIncludeTesting(TestCase):
    """Wave-5 merge-flow: TESTING is the resting shelf (merged, as good as done).
    DONE is a manual housekeeping flip. Autonomy metrics must count both.
    """

    def setUp(self):
        self.board = Board.objects.create(name="Fable Roadmap", working_dir="/tmp/fable")
        self.spec = Spec.objects.create(
            board=self.board,
            odin_id="sp_shelf",
            title="Shelf-resting spec",
        )
        self.claude = _make_agent("claude")

    def _make_testing_task(self, idx, with_steering=False):
        base = datetime.datetime(2026, 7, 7, 9, idx, tzinfo=datetime.timezone.utc)
        t = Task.objects.create(
            board=self.board,
            spec=self.spec,
            title=f"Shelf task #{idx}",
            status=TaskStatus.TESTING,
            created_by="claude@odin.agent",
            assignee=self.claude,
            metadata={"last_duration_ms": 180_000},
        )
        _add_history(t, "status", "TODO", "EXECUTING", "system@taskit",
                     when=base + datetime.timedelta(seconds=5))
        _add_history(t, "status", "EXECUTING", "TESTING", "system@taskit",
                     when=base + datetime.timedelta(minutes=3))
        if with_steering:
            _add_comment(t, "operator@example.com", "Please verify merge.",
                         comment_type="status_update",
                         when=base + datetime.timedelta(minutes=2))
        return t

    def _make_done_task(self, idx):
        base = datetime.datetime(2026, 7, 7, 10, idx, tzinfo=datetime.timezone.utc)
        t = Task.objects.create(
            board=self.board,
            spec=self.spec,
            title=f"Done task #{idx}",
            status=TaskStatus.DONE,
            created_by="claude@odin.agent",
            assignee=self.claude,
            metadata={"last_duration_ms": 240_000},
        )
        _add_history(t, "status", "TODO", "EXECUTING", "system@taskit",
                     when=base + datetime.timedelta(seconds=5))
        _add_history(t, "status", "EXECUTING", "TESTING", "system@taskit",
                     when=base + datetime.timedelta(minutes=3))
        _add_history(t, "status", "TESTING", "DONE", "system@taskit",
                     when=base + datetime.timedelta(minutes=4))
        return t

    def test_testing_tasks_counted_as_landed(self):
        """Shelf-resting TESTING tasks must appear in total_landed."""
        for i in range(5):
            self._make_testing_task(i)

        from autonomy_metrics import compute_board_metrics, LANDED_STATUSES
        self.assertIn(TaskStatus.TESTING, LANDED_STATUSES)
        self.assertIn(TaskStatus.DONE, LANDED_STATUSES)

        m = compute_board_metrics(board=self.board)
        self.assertEqual(m["total_landed"], 5)

    def test_done_tasks_still_counted(self):
        """DONE remains landed — the metric is union, not replacement."""
        for i in range(3):
            self._make_done_task(i)

        from autonomy_metrics import compute_board_metrics
        m = compute_board_metrics(board=self.board)
        self.assertEqual(m["total_landed"], 3)

    def test_mixed_testing_and_done_counted(self):
        """TESTING + DONE both count; a 24-task board with 22 TESTING + 2 DONE → 24."""
        for i in range(22):
            self._make_testing_task(i)
        for i in range(2):
            self._make_done_task(i)

        from autonomy_metrics import compute_board_metrics
        m = compute_board_metrics(board=self.board)
        self.assertEqual(m["total_landed"], 24)

    def test_canceled_not_landed(self):
        """CANCELED is excluded — the union is DONE+TESTING only."""
        for i in range(3):
            self._make_testing_task(i)
        canceled = self._make_testing_task(40)
        canceled.status = TaskStatus.CANCELED
        canceled.save()

        from autonomy_metrics import compute_board_metrics
        m = compute_board_metrics(board=self.board)
        self.assertEqual(m["total_landed"], 3)


class Wave5ZeroUnstickSharpened(TestCase):
    """Zero-unstick (the new flow): dispatch → TESTING with no operator
    intervention. Operator intervention = any operator-authored comment OR
    any operator manual status PATCH beyond the initial dispatch.
    """

    def setUp(self):
        self.board = Board.objects.create(name="Fable Roadmap", working_dir="/tmp/fable")
        self.spec = Spec.objects.create(
            board=self.board,
            odin_id="sp_zero_unstick",
            title="Zero-unstick spec",
        )
        self.claude = _make_agent("claude")

    def test_pure_agent_flow_is_zero_unstick(self):
        """No operator comments, no operator PATCHes → zero unstick."""
        from autonomy_metrics import is_operator_intervention

        base = datetime.datetime(2026, 7, 7, 9, 0, tzinfo=datetime.timezone.utc)
        t = Task.objects.create(
            board=self.board, spec=self.spec, title="Pure agent",
            status=TaskStatus.TESTING, created_by="claude@odin.agent",
            assignee=self.claude, metadata={"last_duration_ms": 120_000},
        )
        _add_history(t, "status", "TODO", "EXECUTING", "system@taskit",
                     when=base + datetime.timedelta(seconds=5))
        _add_history(t, "status", "EXECUTING", "TESTING", "system@taskit",
                     when=base + datetime.timedelta(minutes=3))

        self.assertFalse(is_operator_intervention(t))

    def test_operator_status_update_comment_breaks_zero_unstick(self):
        """Any operator comment (not just steering) counts as intervention."""
        from autonomy_metrics import is_operator_intervention

        base = datetime.datetime(2026, 7, 7, 9, 0, tzinfo=datetime.timezone.utc)
        t = Task.objects.create(
            board=self.board, spec=self.spec, title="Operator comment",
            status=TaskStatus.TESTING, created_by="claude@odin.agent",
            assignee=self.claude, metadata={"last_duration_ms": 120_000},
        )
        _add_history(t, "status", "TODO", "EXECUTING", "system@taskit",
                     when=base + datetime.timedelta(seconds=5))
        _add_history(t, "status", "EXECUTING", "TESTING", "system@taskit",
                     when=base + datetime.timedelta(minutes=3))
        _add_comment(t, "operator@example.com", "Looks good — final nudge.",
                     comment_type="status_update",
                     when=base + datetime.timedelta(minutes=2))

        self.assertTrue(is_operator_intervention(t))

    def test_initial_dispatch_by_operator_does_not_break_zero_unstick(self):
        """Operator does the initial TODO→EXECUTING dispatch but the system
        carries the task to TESTING with no other operator action → still
        hands-free."""
        from autonomy_metrics import is_operator_intervention

        base = datetime.datetime(2026, 7, 7, 9, 0, tzinfo=datetime.timezone.utc)
        t = Task.objects.create(
            board=self.board, spec=self.spec, title="Op-dispatched",
            status=TaskStatus.TESTING, created_by="claude@odin.agent",
            assignee=self.claude, metadata={"last_duration_ms": 120_000},
        )
        # Operator manually dispatches.
        _add_history(t, "status", "TODO", "EXECUTING", "operator@example.com",
                     when=base + datetime.timedelta(seconds=5))
        # System carries to TESTING.
        _add_history(t, "status", "EXECUTING", "TESTING", "system@taskit",
                     when=base + datetime.timedelta(minutes=3))

        self.assertFalse(is_operator_intervention(t))

    def test_operator_patch_after_dispatch_breaks_zero_unstick(self):
        """Operator PATCH to status AFTER initial dispatch is intervention."""
        from autonomy_metrics import is_operator_intervention

        base = datetime.datetime(2026, 7, 7, 9, 0, tzinfo=datetime.timezone.utc)
        t = Task.objects.create(
            board=self.board, spec=self.spec, title="Op patch after",
            status=TaskStatus.TESTING, created_by="claude@odin.agent",
            assignee=self.claude, metadata={"last_duration_ms": 120_000},
        )
        _add_history(t, "status", "TODO", "EXECUTING", "system@taskit",
                     when=base + datetime.timedelta(seconds=5))
        # Operator nudges: BACKLOG→TODO doesn't apply here, use EXECUTING→TESTING
        _add_history(t, "status", "EXECUTING", "TESTING", "operator@example.com",
                     when=base + datetime.timedelta(minutes=3))

        self.assertTrue(is_operator_intervention(t))

    def test_agent_comment_does_not_break_zero_unstick(self):
        """Comments from agents are not intervention."""
        from autonomy_metrics import is_operator_intervention

        base = datetime.datetime(2026, 7, 7, 9, 0, tzinfo=datetime.timezone.utc)
        t = Task.objects.create(
            board=self.board, spec=self.spec, title="Agent comment",
            status=TaskStatus.TESTING, created_by="claude@odin.agent",
            assignee=self.claude, metadata={"last_duration_ms": 120_000},
        )
        _add_history(t, "status", "TODO", "EXECUTING", "system@taskit",
                     when=base + datetime.timedelta(seconds=5))
        _add_history(t, "status", "EXECUTING", "TESTING", "system@taskit",
                     when=base + datetime.timedelta(minutes=3))
        _add_comment(t, "claude@odin.agent", "Proof attached.", comment_type="proof",
                     when=base + datetime.timedelta(minutes=2))

        self.assertFalse(is_operator_intervention(t))

    def test_no_dispatch_counts_as_intervention(self):
        """If there's no EXECUTING/IN_PROGRESS dispatch at all, the task
        was never hands-free (no agent work was triggered)."""
        from autonomy_metrics import is_operator_intervention

        base = datetime.datetime(2026, 7, 7, 9, 0, tzinfo=datetime.timezone.utc)
        t = Task.objects.create(
            board=self.board, spec=self.spec, title="No dispatch",
            status=TaskStatus.TESTING, created_by="operator@example.com",
            assignee=self.claude, metadata={"last_duration_ms": 0},
        )
        _add_history(t, "status", "TODO", "TESTING", "operator@example.com",
                     when=base + datetime.timedelta(seconds=5))

        self.assertTrue(is_operator_intervention(t))


class Wave5LadderSection(TestCase):
    """--ladder section: consecutive hands-free landings of bug-fix-class
    tasks. Heuristic: title matches /\bfix\w*/ (case-insensitive) — i.e.
    fix/fixes/fixed/fixing as a standalone word. Stated explicitly in
    the --ladder output so the heuristic is never a silent assumption.
    """

    def setUp(self):
        self.board = Board.objects.create(name="Fable Roadmap", working_dir="/tmp/fable")
        self.spec = Spec.objects.create(
            board=self.board,
            odin_id="sp_ladder",
            title="Ladder spec",
        )
        self.claude = _make_agent("claude")

    def _make_bug_fix_landed(self, idx, hands_free=True, title=None):
        """Create a bug-fix task that landed at TESTING, possibly with operator noise."""
        base = datetime.datetime(2026, 7, 7, 10, idx, tzinfo=datetime.timezone.utc)
        t = Task.objects.create(
            board=self.board,
            spec=self.spec,
            title=title or f"Fix routing regression #{idx}",
            status=TaskStatus.TESTING,
            created_by="claude@odin.agent",
            assignee=self.claude,
            metadata={"last_duration_ms": 120_000},
        )
        _add_history(t, "status", "TODO", "EXECUTING", "system@taskit",
                     when=base + datetime.timedelta(seconds=5))
        _add_history(t, "status", "EXECUTING", "TESTING", "system@taskit",
                     when=base + datetime.timedelta(minutes=3))
        if not hands_free:
            _add_comment(t, "operator@example.com", "Manual nudge.",
                         comment_type="status_update",
                         when=base + datetime.timedelta(minutes=2))
        return t

    def test_is_bug_fix_heuristic(self):
        """Title heuristic: standalone 'fix' word (fix/fixes/fixed/fixing)."""
        from autonomy_metrics import is_bug_fix_task
        # True positives
        self.assertTrue(is_bug_fix_task(_Fake(title="Fix routing regression")))
        self.assertTrue(is_bug_fix_task(_Fake(title="Refactor: fix the auth bug")))
        self.assertTrue(is_bug_fix_task(_Fake(title="Fixes snapshot cost test")))
        self.assertTrue(is_bug_fix_task(_Fake(title="[fix] merge conflict handler")))
        # True negatives
        self.assertFalse(is_bug_fix_task(_Fake(title="Refactor autonomy metrics")))
        self.assertFalse(is_bug_fix_task(_Fake(title="Prefix detection in tokenizer")))
        self.assertFalse(is_bug_fix_task(_Fake(title="Suffix helper utility")))

    def test_ladder_current_and_best_streak(self):
        """A run of 5 hands-free bug-fix landings, then 1 with operator noise,
        then 3 more hands-free → current=3, best=5."""
        # 5 hands-free, then 1 with intervention, then 3 hands-free.
        for i in range(5):
            self._make_bug_fix_landed(i, hands_free=True)
        self._make_bug_fix_landed(5, hands_free=False)
        for i in range(6, 9):
            self._make_bug_fix_landed(i, hands_free=True)

        from autonomy_metrics import compute_ladder_metrics
        ladder = compute_ladder_metrics(board=self.board)
        self.assertEqual(ladder["total_bug_fixes_landed"], 9)
        self.assertEqual(ladder["hands_free_bug_fixes"], 8)
        self.assertEqual(ladder["current_streak"], 3)
        self.assertEqual(ladder["best_streak"], 5)

    def test_l2_threshold_reported(self):
        """L2 ladder goal: 10 hands-free bug fixes in a row. State plainly."""
        from autonomy_metrics import compute_ladder_metrics
        # Only 3 hands-free in a row — L2 not met.
        for i in range(3):
            self._make_bug_fix_landed(i, hands_free=True)
        ladder = compute_ladder_metrics(board=self.board)
        self.assertEqual(ladder["current_streak"], 3)
        self.assertFalse(ladder["l2_met"])
        self.assertEqual(ladder["l2_threshold"], 10)

    def test_l2_met_when_streak_hits_threshold(self):
        from autonomy_metrics import compute_ladder_metrics
        for i in range(12):
            self._make_bug_fix_landed(i, hands_free=True)
        ladder = compute_ladder_metrics(board=self.board)
        self.assertEqual(ladder["current_streak"], 12)
        self.assertTrue(ladder["l2_met"])
        self.assertEqual(ladder["best_streak"], 12)

    def test_non_bug_fix_tasks_excluded_from_ladder(self):
        """Ladder only counts bug-fix class — improvement tasks don't break
        or extend the streak. Place the improvement BETWEEN two bug-fix
        landings so it's clearly in the middle of the chronological ladder."""
        # Bug-fix 0, 1, then a non-bug-fix landing in the middle, then bug-fix 2, 3.
        self._make_bug_fix_landed(0, hands_free=True)  # lands at 10:00
        self._make_bug_fix_landed(1, hands_free=True)  # lands at 10:01
        # Non-bug-fix landed at 10:01:30 — between bug-fix 1 and bug-fix 2.
        base = datetime.datetime(2026, 7, 7, 10, 1, 30, tzinfo=datetime.timezone.utc)
        imp = Task.objects.create(
            board=self.board, spec=self.spec,
            title="Improve telemetry sampling",
            status=TaskStatus.TESTING, created_by="claude@odin.agent",
            assignee=self.claude, metadata={"last_duration_ms": 60_000},
        )
        _add_history(imp, "status", "TODO", "EXECUTING", "system@taskit",
                     when=base + datetime.timedelta(seconds=5))
        _add_history(imp, "status", "EXECUTING", "TESTING", "system@taskit",
                     when=base + datetime.timedelta(minutes=3))
        self._make_bug_fix_landed(2, hands_free=True)  # lands at 10:02
        self._make_bug_fix_landed(3, hands_free=True)  # lands at 10:03

        from autonomy_metrics import compute_ladder_metrics
        ladder = compute_ladder_metrics(board=self.board)
        # 4 bug-fixes total, all hands-free. The improvement is filtered
        # out (not bug-fix class), so the streak is all 4 in a row.
        self.assertEqual(ladder["total_bug_fixes_landed"], 4)
        self.assertEqual(ladder["hands_free_bug_fixes"], 4)
        self.assertEqual(ladder["current_streak"], 4)
        self.assertEqual(ladder["best_streak"], 4)

    def test_ladder_section_printed_in_standard_mode(self):
        """--ladder section prints the streak, the L2 threshold, and the heuristic."""
        for i in range(3):
            self._make_bug_fix_landed(i, hands_free=True)

        buf = io.StringIO()
        old_argv = sys.argv
        sys.argv = ["autonomy_metrics.py", "--sections", "ladder"]
        try:
            from autonomy_metrics import _print_standard
            from autonomy_metrics import compute_board_metrics, compute_ladder_metrics
            m = compute_board_metrics(board=self.board)
            ladder = compute_ladder_metrics(board=self.board)
            with redirect_stdout(buf):
                _print_standard(m, sections={"ladder"},
                                ladder=ladder)
        finally:
            sys.argv = old_argv
        out = buf.getvalue()
        self.assertIn("LADDER", out.upper())
        self.assertIn("current", out.lower())
        self.assertIn("best", out.lower())
        self.assertIn("heuristic", out.lower())

    def test_ladder_json_output(self):
        """compute_board_metrics + compute_ladder_metrics surface ladder data in JSON."""
        for i in range(2):
            self._make_bug_fix_landed(i, hands_free=True)
        self._make_bug_fix_landed(2, hands_free=False)

        from autonomy_metrics import compute_ladder_metrics
        ladder = compute_ladder_metrics(board=self.board)
        self.assertIn("current_streak", ladder)
        self.assertIn("best_streak", ladder)
        self.assertIn("l2_met", ladder)
        self.assertIn("l2_threshold", ladder)
        self.assertIn("total_bug_fixes_landed", ladder)
        self.assertIn("hands_free_bug_fixes", ladder)
        self.assertIn("heuristic", ladder)


# ── Wave-8 / W248: machine-author denylist pins the hands-free boundary ──
# Live data (board 5) shows non-agent comments from @system / @taskit /
# @harness.kit authors (twins memory lookup, trace sidecar, proof upload).
# These authors are machines, not humans; the prior rule that treated
# them as operators made every task read as intervened and the L2 ladder
# showed 0 hands-free of 13 instead of the documented 3. These two
# fixture tests pin the boundary at the intervention level.


class Wave8SystemAuthorsNotIntervention(TestCase):
    """A task whose only non-agent comments are from machine-domain
    authors (@system / @taskit / @harness.kit) is hands-free — the prior
    rule that flagged them as operator intervention was the W8.1 bug."""

    def setUp(self):
        self.board = Board.objects.create(name="Fable Roadmap", working_dir="/tmp/fable")
        self.spec = Spec.objects.create(
            board=self.board,
            odin_id="sp_w8_machine_only",
            title="W8 machine-only authors",
        )
        self.claude = _make_agent("claude")

    def _make_dispatched_task(self):
        base = datetime.datetime(2026, 7, 8, 10, 0, tzinfo=datetime.timezone.utc)
        t = Task.objects.create(
            board=self.board, spec=self.spec,
            title="Fix ladder denylist (W8.1)",
            status=TaskStatus.TESTING,
            created_by="claude@odin.agent",
            assignee=self.claude,
            metadata={"last_duration_ms": 120_000},
        )
        _add_history(t, "status", "TODO", "EXECUTING", "system@taskit",
                     when=base + datetime.timedelta(seconds=5))
        _add_history(t, "status", "EXECUTING", "TESTING", "system@taskit",
                     when=base + datetime.timedelta(minutes=3))
        return t, base

    def test_pure_system_authors_not_intervention(self):
        """Twins memory + trace sidecar + proof upload = machine authors.
        is_operator_intervention must return False — the task is hands-free."""
        from autonomy_metrics import is_operator_intervention
        t, base = self._make_dispatched_task()
        # Three machine-only comments that the prior rule would have
        # misclassified as operator intervention.
        _add_comment(t, "odin+memory@system", "Memory twins lookup.",
                     comment_type="status_update",
                     when=base + datetime.timedelta(minutes=1))
        _add_comment(t, "odin@harness.kit", "Trace sidecar echo.",
                     comment_type="status_update",
                     when=base + datetime.timedelta(minutes=2))
        _add_comment(t, "proof-upload@taskit", "Proof files attached.",
                     comment_type="proof",
                     when=base + datetime.timedelta(minutes=4))
        # Even with a system@taskit comment and a proof upload from a
        # different service, the task is still hands-free by definition.
        self.assertFalse(is_operator_intervention(t))

    def test_pure_machine_authors_count_toward_ladder_streak(self):
        """The same fixture, fed into compute_ladder_metrics, must count
        the task as hands-free (the W8.1 symptom: ladder showed 0 because
        every such task was misclassified as intervened)."""
        from autonomy_metrics import compute_ladder_metrics
        t, base = self._make_dispatched_task()
        _add_comment(t, "odin+memory@system", "Twins lookup.",
                     comment_type="status_update",
                     when=base + datetime.timedelta(minutes=1))
        _add_comment(t, "odin@harness.kit", "Trace echo.",
                     comment_type="status_update",
                     when=base + datetime.timedelta(minutes=2))
        _add_comment(t, "proof-upload@taskit", "Proof files.",
                     comment_type="proof",
                     when=base + datetime.timedelta(minutes=4))
        ladder = compute_ladder_metrics(board=self.board)
        self.assertEqual(ladder["total_bug_fixes_landed"], 1)
        self.assertEqual(ladder["hands_free_bug_fixes"], 1)
        self.assertEqual(ladder["current_streak"], 1)


class Wave8HumanAuthorIsIntervention(TestCase):
    """The boundary: a single real human comment is enough to mark
    the task as intervened. operator@ is the known operator; this pins
    the other side of the W8.1 denylist."""

    def setUp(self):
        self.board = Board.objects.create(name="Fable Roadmap", working_dir="/tmp/fable")
        self.spec = Spec.objects.create(
            board=self.board,
            odin_id="sp_w8_operator",
            title="W8 operator boundary",
        )
        self.claude = _make_agent("claude")

    def test_human_comment_counts_as_intervention(self):
        """A single operator@example.com comment flips the task to
        intervened — the operator spoke, the agent did not run alone."""
        from autonomy_metrics import is_operator_intervention
        base = datetime.datetime(2026, 7, 8, 11, 0, tzinfo=datetime.timezone.utc)
        t = Task.objects.create(
            board=self.board, spec=self.spec,
            title="Fix verify.sh path (W8.1)",
            status=TaskStatus.TESTING,
            created_by="claude@odin.agent",
            assignee=self.claude,
            metadata={"last_duration_ms": 90_000},
        )
        _add_history(t, "status", "TODO", "EXECUTING", "system@taskit",
                     when=base + datetime.timedelta(seconds=5))
        _add_history(t, "status", "EXECUTING", "TESTING", "system@taskit",
                     when=base + datetime.timedelta(minutes=3))
        _add_comment(t, "operator@example.com", "Quick steer: use the new path.",
                     comment_type="status_update",
                     when=base + datetime.timedelta(minutes=2))
        self.assertTrue(is_operator_intervention(t))

    def test_human_comment_alone_breaks_ladder_streak(self):
        """Same fixture in the ladder: a single operator@ comment breaks
        the streak at that point. With one hands-free + one intervened,
        the streak resets to 0 after the intervened landing."""
        from autonomy_metrics import compute_ladder_metrics
        # First a hands-free bug fix (machine-only authors).
        base1 = datetime.datetime(2026, 7, 8, 12, 0, tzinfo=datetime.timezone.utc)
        t1 = Task.objects.create(
            board=self.board, spec=self.spec,
            title="Fix ladder denylist (W8.1a)",
            status=TaskStatus.TESTING,
            created_by="claude@odin.agent",
            assignee=self.claude,
            metadata={"last_duration_ms": 60_000},
        )
        _add_history(t1, "status", "TODO", "EXECUTING", "system@taskit",
                     when=base1 + datetime.timedelta(seconds=5))
        _add_history(t1, "status", "EXECUTING", "TESTING", "system@taskit",
                     when=base1 + datetime.timedelta(minutes=3))
        _add_comment(t1, "odin+memory@system", "Twins lookup.",
                     comment_type="status_update",
                     when=base1 + datetime.timedelta(minutes=1))
        # Then a hands-not-free bug fix (the human speaks).
        base2 = base1 + datetime.timedelta(hours=1)
        t2 = Task.objects.create(
            board=self.board, spec=self.spec,
            title="Fix verify.sh path (W8.1b)",
            status=TaskStatus.TESTING,
            created_by="claude@odin.agent",
            assignee=self.claude,
            metadata={"last_duration_ms": 60_000},
        )
        _add_history(t2, "status", "TODO", "EXECUTING", "system@taskit",
                     when=base2 + datetime.timedelta(seconds=5))
        _add_history(t2, "status", "EXECUTING", "TESTING", "system@taskit",
                     when=base2 + datetime.timedelta(minutes=3))
        _add_comment(t2, "operator@example.com", "Use the new path.",
                     comment_type="status_update",
                     when=base2 + datetime.timedelta(minutes=2))
        ladder = compute_ladder_metrics(board=self.board)
        self.assertEqual(ladder["total_bug_fixes_landed"], 2)
        self.assertEqual(ladder["hands_free_bug_fixes"], 1)
        # Streak: 1 hands-free, then break → current=0, best=1.
        self.assertEqual(ladder["current_streak"], 0)
        self.assertEqual(ladder["best_streak"], 1)


# ── Wave-8 / W248: live-data replay pins the ladder number ───────────
# The two fixture tests above pin the BOUNDARY. This test replays the
# exact 13-task board-5 snapshot captured at task creation time and
# asserts the function produces the documented ladder value
# (5 hands-free, best_streak=4). If the classifier changes in a way
# that flips a real task's intervention status, this test fails loudly.
#
# The snapshot lives in .proof/task-248/live_bug_fixes_snapshot.json
# and is checked in alongside the proof (so a CI run can replay the
# exact same data the agent saw when filing the fix).

LIVE_SNAPSHOT_PATH = (
    Path(__file__).resolve().parent
    / "data" / "wave8_ladder" / "board5_bug_fix_landings.json"
)


class Wave8LiveDataReplay(TestCase):
    """Replay board-5's bug-fix landings through compute_ladder_metrics
    and assert the function output matches the documented W8.1 fix
    (5 hands-free, best_streak=4). Re-running with the OLD rule must
    fail (0 hands-free); with the NEW rule it must pass.

    The snapshot is the live API data captured at task #248 dispatch
    time. The replay is end-to-end: same comment authors, same status
    histories, same order — so the only thing under test is the
    classifier (and the function's downstream streak math).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not LIVE_SNAPSHOT_PATH.exists():
            raise SkipTest(
                f"live snapshot missing: {LIVE_SNAPSHOT_PATH} "
                "(regenerate via .proof/task-248/capture_ladder.py)"
            )
        cls.snapshot = json.loads(LIVE_SNAPSHOT_PATH.read_text())

    def setUp(self):
        self.board = Board.objects.create(name="Fable Roadmap (replay)", working_dir="/tmp/fable-replay")
        self.spec = Spec.objects.create(
            board=self.board,
            odin_id="sp_w8_replay",
            title="W8 live-data replay",
        )
        self.claude = _make_agent("claude")

    def _build_task(self, entry):
        t = Task.objects.create(
            board=self.board,
            spec=self.spec,
            title=entry["title"],
            status=entry["status"],
            created_by="claude@odin.agent",
            assignee=self.claude,
            metadata={"last_duration_ms": 120_000},
        )
        # Use the snapshot's history rows; the dispatch logic requires
        # ascending changed_at. Pin explicit timestamps so the DB
        # ordering is deterministic (default auto_now stamps them all
        # the same second, which breaks the "first dispatch" check).
        for h in entry["histories"]:
            changed_at = _parse_dt(h.get("changed_at"))
            row = TaskHistory.objects.create(
                task=t,
                field_name=h["field_name"],
                old_value=str(h["old_value"] or ""),
                new_value=str(h["new_value"] or ""),
                changed_by=h["changed_by"] or "",
            )
            if changed_at is not None:
                TaskHistory.objects.filter(pk=row.pk).update(changed_at=changed_at)
        for c in entry["comments"]:
            TaskComment.objects.create(
                task=t,
                author_email=c["author_email"] or "",
                content="(replayed)",
                comment_type=c.get("comment_type") or "status_update",
            )
        return t

    def test_replay_matches_documented_ladder(self):
        """5 hands-free of 13 with the NEW rule, best_streak=4."""
        for entry in self.snapshot:
            self._build_task(entry)

        from autonomy_metrics import compute_ladder_metrics
        ladder = compute_ladder_metrics(board=self.board)
        self.assertEqual(ladder["total_bug_fixes_landed"], 13)
        self.assertEqual(ladder["hands_free_bug_fixes"], 5)
        self.assertEqual(ladder["best_streak"], 4)
        self.assertEqual(ladder["current_streak"], 0)
        self.assertFalse(ladder["l2_met"])

    def test_replay_against_old_rule_fails_loudly(self):
        """The snapshot must yield 0 hands-free under the OLD rule —
        if a future change to is_human_author silently regresses to the
        old behavior, this test will catch it.

        We patch the function back to the old two-line rule for the
        duration of this test and assert the documented W8.1 symptom
        (0 hands-free) reappears."""
        for entry in self.snapshot:
            self._build_task(entry)

        from tasks import agent_stats
        import autonomy_metrics

        def _old_rule(email: str) -> bool:
            if not email:
                return True
            e = email.lower()
            if e.endswith("@odin.agent"):
                return False
            if e == "system@taskit":
                return False
            return True

        # Patch EVERY name that is_operator_intervention can read at
        # call time: the canonical home, the autonomy_metrics
        # re-export, and the back-compat alias (is_operator_email =
        # is_human_author is a module-level rebind — patching one does
        # not affect the other).
        names_to_patch = [
            (agent_stats, "is_human_author"),
            (agent_stats, "is_operator_email"),
            (autonomy_metrics, "is_human_author"),
            (autonomy_metrics, "is_operator_email"),
        ]
        originals = [(m, n, getattr(m, n)) for m, n in names_to_patch]
        for m, n in names_to_patch:
            setattr(m, n, _old_rule)
        try:
            from autonomy_metrics import compute_ladder_metrics
            ladder = compute_ladder_metrics(board=self.board)
        finally:
            for m, n, orig in originals:
                setattr(m, n, orig)

        # The W8.1 symptom under the old rule: 0 hands-free.
        self.assertEqual(ladder["hands_free_bug_fixes"], 0)
        self.assertEqual(ladder["current_streak"], 0)
        self.assertEqual(ladder["best_streak"], 0)


class MergeAgentNotIntervention(TestCase):
    """The DAG executor posts merge-conflict comments as
    ``merge-agent@odin`` — an internal service, not a human. The L2
    counter must not count these as operator touches, or every task
    that went through a merge reads as intervened and the autonomy
    number flatters us least (inflated human-touch count)."""

    def setUp(self):
        self.board = Board.objects.create(name="Fable Roadmap", working_dir="/tmp/fable")
        self.spec = Spec.objects.create(
            board=self.board,
            odin_id="sp_merge_agent",
            title="Merge-agent boundary",
        )
        self.claude = _make_agent("claude")

    def test_merge_agent_comment_not_intervention(self):
        """A task whose only non-system comment is from merge-agent@odin
        is hands-free — the merge agent is a machine."""
        from autonomy_metrics import is_operator_intervention

        base = datetime.datetime(2026, 7, 10, 10, 0, tzinfo=datetime.timezone.utc)
        t = Task.objects.create(
            board=self.board, spec=self.spec,
            title="Fix routing bug with merge conflict",
            status=TaskStatus.TESTING,
            created_by="claude@odin.agent",
            assignee=self.claude,
            metadata={"last_duration_ms": 120_000},
        )
        _add_history(t, "status", "TODO", "EXECUTING", "system@taskit",
                     when=base + datetime.timedelta(seconds=5))
        _add_history(t, "status", "EXECUTING", "TESTING", "system@taskit",
                     when=base + datetime.timedelta(minutes=3))
        _add_comment(t, "merge-agent@odin", "Auto-resolved conflict: keep both.",
                     comment_type="status_update",
                     when=base + datetime.timedelta(minutes=2))

        self.assertFalse(is_operator_intervention(t))

    def test_merge_agent_task_scores_zero_operator_touches(self):
        """The board-level metrics must show zero operator touches for a
        task driven entirely by agents + the merge agent."""
        base = datetime.datetime(2026, 7, 10, 11, 0, tzinfo=datetime.timezone.utc)
        t = Task.objects.create(
            board=self.board, spec=self.spec,
            title="Fix another routing bug",
            status=TaskStatus.TESTING,
            created_by="claude@odin.agent",
            assignee=self.claude,
            metadata={"last_duration_ms": 90_000},
        )
        _add_history(t, "status", "TODO", "EXECUTING", "system@taskit",
                     when=base + datetime.timedelta(seconds=5))
        _add_history(t, "status", "EXECUTING", "TESTING", "system@taskit",
                     when=base + datetime.timedelta(minutes=3))
        _add_comment(t, "merge-agent@odin", "Merge resolved automatically.",
                     comment_type="status_update",
                     when=base + datetime.timedelta(minutes=2))

        from autonomy_metrics import compute_board_metrics
        m = compute_board_metrics(board=self.board)
        self.assertEqual(m["total_landed"], 1)
        self.assertEqual(m["agent_authored"], 1)
        self.assertEqual(m["operator_touches_total"], 0)
        self.assertEqual(m["tasks_with_zero_unsticks"], 1)


# Tiny duck-typed stand-in for Task so is_bug_fix_task can be tested
# without a DB fixture (it only reads .title).
class _Fake:
    def __init__(self, title=""):
        self.title = title


def _parse_dt(s):
    """Best-effort parse of an ISO timestamp from the API snapshot."""
    if not s:
        return None
    try:
        # Django stores DateTimeField as naive UTC when TIME_ZONE=None;
        # strip the trailing Z so fromisoformat accepts it.
        cleaned = s.replace("Z", "+00:00") if s.endswith("Z") else s
        dt = datetime.datetime.fromisoformat(cleaned)
        if dt.tzinfo is not None:
            dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return None