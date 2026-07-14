"""Tests for the Memory "quote" feature: turn twins into an estimate.

Builds on tasks/similarity.py — takes the twins a task already scored and
turns them into a robust quote (median tokens, median duration) with a
stated confidence tier. Posts the quote in the same dispatch comment
(extends it, never a second comment), stamps estimate vs actual into
task.metadata at DONE / auto-promotion (TESTING), and exposes a one-liner
audit number on the task-detail API.

Scenario matrix:
  - median aggregation (median of [10, 20, 30] = 20, not 20)
  - robust to outliers: [100, 110, 1_000_000] still yields ~110, not 333370
  - confidence tiers: 0 twins → "none" (no estimate, never invented)
  - 1 twin → low, 2 twins → medium, 3+ twins → high
  - drop None values from the median (a twin missing tokens still scores)
  - no-twin degradation: when there are 0 twins the quote line is omitted
    entirely from the comment and metadata.estimate is never written.
  - estimate is stamped at dispatch; actual is stamped at DONE / TESTING;
    they co-exist and can be diffed
  - a one-line "actual vs quote" trail lands in the dispatch comment on
    auto-promotion (TESTING) and DONE — never a second comment

The quote_accuracy diagnostic (testing_tools/quote_accuracy.py) is exercised
end-to-end via tests/test_quote.py — no separate script test file, the
script is a thin ORM reader that gets most of its surface from the same
module as the script logic.
"""

import json
import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import MagicMock, patch

from tasks.dag_executor import poll_and_execute
from tasks.estimation import (
    CONFIDENCE_NONE,
    compute_estimate,
    format_estimate_line,
)
from tasks.models import CommentType, Task, TaskComment, TaskHistory, TaskStatus
from tasks.similarity import find_twins, post_twins_comment
from tests.base import APITestCase


def _make_trace_comment(task, input_tokens=38, output_tokens=925):
    """Attach a fake execution trace so compute_usage_from_trace() has data."""
    raw_output = "\n".join([
        '{"type":"text","part":{"text":"-------ODIN-STATUS-------\\nSUCCESS\\n-------ODIN-SUMMARY-------\\nDone."}}',
        (
            '{"modelUsage":{"claude-sonnet-4-5":{"inputTokens":%d,"outputTokens":%d}}}'
            % (input_tokens, output_tokens)
        ),
    ])
    TaskComment.objects.create(
        task=task,
        author_email="odin@system",
        content=raw_output,
        attachments=["trace:execution_jsonl"],
    )


def _twin(task_id, tokens, duration_ms, score=0.5, redo_rounds=0):
    """Build a twin-shaped dict like similarity._serialize_twin returns."""
    return {
        "task_id": task_id,
        "title": f"#{task_id}",
        "outcome": TaskStatus.DONE,
        "tokens": tokens,
        "duration_ms": duration_ms,
        "redo_rounds": redo_rounds,
        "agent": "Claude",
        "score": score,
        "text_score": score,
        "structural_score": 0.0,
        "proof_path": f".proof/task-{task_id}/proof.md",
    }


class ComputeEstimateMathTests(APITestCase):
    """Pure-function tests for the estimate math (no DB)."""

    def test_median_of_three(self):
        estimate = compute_estimate([
            _twin(1, tokens=100, duration_ms=10_000),
            _twin(2, tokens=200, duration_ms=20_000),
            _twin(3, tokens=300, duration_ms=30_000),
        ])
        self.assertEqual(estimate["tokens_median"], 200)
        self.assertEqual(estimate["duration_ms_median"], 20_000)

    def test_median_robust_to_outlier(self):
        """One massive outlier must not dominate the median."""
        estimate = compute_estimate([
            _twin(1, tokens=100, duration_ms=10_000),
            _twin(2, tokens=110, duration_ms=11_000),
            _twin(3, tokens=1_000_000, duration_ms=9_000_000),
        ])
        self.assertEqual(estimate["tokens_median"], 110)
        self.assertEqual(estimate["duration_ms_median"], 11_000)

    def test_median_drops_none(self):
        """A twin missing tokens or duration must not skew the median."""
        # Odd count after dropping None so the median is unambiguous.
        estimate = compute_estimate([
            _twin(1, tokens=None, duration_ms=10_000),
            _twin(2, tokens=200, duration_ms=None),
            _twin(3, tokens=300, duration_ms=30_000),
            _twin(4, tokens=400, duration_ms=40_000),
        ])
        self.assertEqual(estimate["tokens_median"], 300)
        self.assertEqual(estimate["duration_ms_median"], 30_000)

    def test_all_metrics_missing_yields_none(self):
        """Every twin is missing → tokens/duration median are None, not 0."""
        estimate = compute_estimate([
            _twin(1, tokens=None, duration_ms=None),
            _twin(2, tokens=None, duration_ms=None),
        ])
        self.assertIsNone(estimate["tokens_median"])
        self.assertIsNone(estimate["duration_ms_median"])
        self.assertEqual(estimate["twin_count"], 2)

    def test_confidence_tiers(self):
        """0 → none, 1 → low, 2 → medium, 3+ → high."""
        self.assertEqual(compute_estimate([])["confidence"], CONFIDENCE_NONE)
        self.assertEqual(
            compute_estimate([_twin(1, 100, 1_000)])["confidence"], "low"
        )
        self.assertEqual(
            compute_estimate([_twin(1, 100, 1_000), _twin(2, 200, 2_000)])["confidence"],
            "medium",
        )
        self.assertEqual(
            compute_estimate([
                _twin(1, 100, 1_000),
                _twin(2, 200, 2_000),
                _twin(3, 300, 3_000),
            ])["confidence"],
            "high",
        )

    def test_no_twin_returns_none_confidence_and_no_invented_numbers(self):
        """0 twins → confidence "none", and the only valid keys are twin_count=0 + confidence="none"."""
        estimate = compute_estimate([])
        self.assertEqual(estimate["confidence"], CONFIDENCE_NONE)
        self.assertEqual(estimate["twin_count"], 0)
        self.assertNotIn("tokens_median", estimate)
        self.assertNotIn("duration_ms_median", estimate)
        self.assertNotIn("source_twin_ids", estimate)

    def test_source_twin_ids_preserved(self):
        """The estimate records which twins it was built from."""
        estimate = compute_estimate([
            _twin(11, 100, 1_000),
            _twin(12, 200, 2_000),
        ])
        self.assertEqual(set(estimate["source_twin_ids"]), {11, 12})


class FormatEstimateLineTests(APITestCase):
    """The line that gets appended to the dispatch comment."""

    def test_quote_with_tokens_and_duration(self):
        estimate = {
            "confidence": "high",
            "twin_count": 3,
            "tokens_median": 2_500_000,
            "duration_ms_median": 900_000,
        }
        line = format_estimate_line(estimate)
        self.assertIn("Estimate", line)
        self.assertIn("high", line)
        self.assertIn("3 twins", line)
        self.assertIn("15 min", line)
        self.assertIn("2.5M tokens", line)

    def test_quote_with_only_tokens(self):
        estimate = {
            "confidence": "medium",
            "twin_count": 2,
            "tokens_median": 1_000_000,
            "duration_ms_median": None,
        }
        line = format_estimate_line(estimate)
        self.assertIn("1.0M tokens", line)
        self.assertNotIn("min", line.lower() or line)

    def test_no_twin_degrades_to_no_estimate(self):
        """The line must say 'no estimate' verbatim, not invent a number."""
        estimate = {"confidence": CONFIDENCE_NONE, "twin_count": 0}
        line = format_estimate_line(estimate)
        self.assertIn("no estimate", line.lower())


class PostTwinsCommentWithQuoteTests(APITestCase):
    """The dispatch comment must carry the quote — one comment, not two."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(allow_project_root_execution=True)
        self.user = self.make_user()

    @patch("tasks.dag_executor.execute_single_task")
    def test_dispatch_comment_contains_quote_line(self, mock_exec):
        mock_exec.delay.return_value = MagicMock(id="fake-celery-quote-1")
        # Three twins with similar titles/descriptions so find_twins actually
        # surfaces them — the test exercises the dispatch comment contract,
        # not the similarity scorer (that's covered by test_memory_twins).
        for i, (tokens, dur) in enumerate([(1_000, 60_000), (1_200, 72_000), (1_400, 84_000)], start=1):
            twin = self.make_task(
                self.board,
                title=f"Build quoteable feature {i}",
                description="Fix login redirect bug so users on Safari land on /404.",
                status=TaskStatus.DONE,
                metadata={"last_duration_ms": float(dur)},
            )
            _make_trace_comment(twin, input_tokens=int(tokens / 2), output_tokens=int(tokens / 2))

        task = self.make_task(
            self.board,
            title="Build quoteable feature on Firefox",
            description="Fix login redirect bug so users on Firefox land on /404 too.",
            status=TaskStatus.IN_PROGRESS,
            assignee=self.user,
            depends_on=[],
        )

        poll_and_execute()

        task.refresh_from_db()
        comments = TaskComment.objects.filter(task=task, author_label="memory")
        self.assertEqual(comments.count(), 1, "must be exactly one comment — not two")
        content = comments.first().content
        self.assertIn("Memory — closest finished twins", content)
        self.assertIn("Estimate", content)
        # Estimate must reference the actual twin tokens (median of 1000, 1200, 1400 = 1200)
        self.assertIn("1.2K", content)
        # Estimate must reference the median duration (72_000 ms = 1.2 min)
        self.assertIn("1.2 min", content)
        # Confidence tier
        self.assertIn("high", content)
        # Stamp landed on metadata
        self.assertIn("estimate", task.metadata)
        est = task.metadata["estimate"]
        self.assertEqual(est["tokens_median"], 1200)
        self.assertEqual(est["duration_ms_median"], 72_000)
        self.assertEqual(est["confidence"], "high")
        self.assertEqual(set(est["source_twin_ids"]),
                         {t.id for t in Task.objects.filter(
                             title__regex=r"^Build quoteable feature [123]$",
                         )})

    @patch("tasks.dag_executor.execute_single_task")
    def test_dispatch_with_no_history_posts_no_quote(self, mock_exec):
        """No twins → no comment at all. Never a 'no estimate' comment on its own."""
        mock_exec.delay.return_value = MagicMock(id="fake-celery-quote-2")
        task = self.make_task(
            self.board,
            status=TaskStatus.IN_PROGRESS,
            assignee=self.user,
            depends_on=[],
        )

        poll_and_execute()

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.EXECUTING)
        self.assertEqual(
            TaskComment.objects.filter(task=task, author_label="memory").count(), 0,
            "no twins → no memory comment, no quote",
        )
        # And no estimate stamped either — that would be inventing one.
        self.assertNotIn("estimate", task.metadata)


class StampingActualAtCompletionTests(APITestCase):
    """estimate vs actual stamping at the two terminal-ish transitions.

    "Auto-promotion" = the W5 REVIEW → TESTING advance (the system never
    auto-DONEs). DONE is the manual flip after the operator confirms. The
    stamp must happen in BOTH paths so quote_accuracy has rows to read.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board, odin_id="sp_q", title="Q")

    def _task_with_dispatch_quote(self, status, **overrides):
        defaults = dict(
            spec=self.spec,
            status=status,
            metadata={
                "branch": "task/sp_q/202",
                "last_duration_ms": 90_000.0,
                "estimate": {
                    "tokens_median": 1_000,
                    "duration_ms_median": 60_000,
                    "confidence": "high",
                    "twin_count": 3,
                    "source_twin_ids": [1, 2, 3],
                },
            },
        )
        defaults.update(overrides)
        task = self.make_task(self.board, **defaults)
        _make_trace_comment(task, input_tokens=500, output_tokens=600)
        return task

    def test_advance_to_testing_stamps_actual(self):
        from tasks.dag_executor import _advance_task_to_testing

        task = self._task_with_dispatch_quote(status=TaskStatus.REVIEW)
        _advance_task_to_testing(task)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.TESTING)
        self.assertIn("actual", task.metadata)
        act = task.metadata["actual"]
        self.assertEqual(act["tokens"], 1100)
        self.assertEqual(act["duration_ms"], 90_000.0)
        self.assertEqual(act["transition"], "auto_promote_testing")

    def test_advance_to_testing_appends_quote_line_to_dispatch_comment(self):
        from tasks.dag_executor import _advance_task_to_testing

        task = self._task_with_dispatch_quote(status=TaskStatus.REVIEW)
        # Simulate the dispatch-time twins comment that carried the quote.
        dispatch_comment = TaskComment.objects.create(
            task=task,
            author_email="odin+memory@system",
            author_label="memory",
            content="**Memory — closest finished twins:**\n1. #1 ... (high)\nEstimate: ~1.0 min, ~1.0K tokens (high)",
            comment_type=CommentType.STATUS_UPDATE,
        )

        _advance_task_to_testing(task)

        dispatch_comment.refresh_from_db()
        self.assertIn("Actual", dispatch_comment.content)
        self.assertIn("1.1K", dispatch_comment.content)
        # Same comment got the line — never a second one.
        self.assertEqual(
            TaskComment.objects.filter(task=task, author_label="memory").count(), 1,
        )

    def test_spec_finalize_done_stamps_actual(self):
        from tasks.dag_executor import _transition_spec_tasks_to_done

        task = self._task_with_dispatch_quote(status=TaskStatus.TESTING)
        _transition_spec_tasks_to_done(self.spec, pr_url="https://github.com/foo/bar/pull/1")

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.DONE)
        act = task.metadata["actual"]
        self.assertEqual(act["transition"], "spec_finalize_done")
        self.assertEqual(act["tokens"], 1100)
        self.assertEqual(act["duration_ms"], 90_000.0)

    def test_api_patch_to_done_stamps_actual(self):
        """The PATCH-driven DONE transition (operator manual flip) also stamps."""
        task = self._task_with_dispatch_quote(status=TaskStatus.TESTING)
        resp = self.client.patch(
            f"/tasks/{task.id}/",
            data=json.dumps({"status": "DONE", "updated_by": "alice@test.com"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, f"PATCH returned {resp.status_code}: {resp.content!r}")
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertIn("actual", task.metadata)
        act = task.metadata["actual"]
        self.assertEqual(act["transition"], "api_patch_done")
        self.assertEqual(act["tokens"], 1100)


class QuoteAccuracyDiagnosticTests(APITestCase):
    """The quote_accuracy diagnostic reads task.metadata for the audit number."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        # The diagnostic script does `from _utils import ...` which only
        # resolves when its own directory is on sys.path. Other scripts in
        # this directory follow the same pattern (see board_overview.py).
        import os
        import sys
        self._testing_tools_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "testing_tools",
        )
        if self._testing_tools_dir not in sys.path:
            sys.path.insert(0, self._testing_tools_dir)

    def test_quote_accuracy_reports_each_finished_task(self):
        # Three finished tasks each carrying estimate + actual metadata.
        rows = [
            (1, 1000, 60_000, 1200, 90_000),
            (2, 2000, 120_000, 1900, 110_000),
            (3, 3000, 180_000, 2900, 200_000),
        ]
        for i, est_t, est_d, act_t, act_d in rows:
            t = self.make_task(
                self.board,
                title=f"Done task {i}",
                status=TaskStatus.DONE,
                metadata={
                    "last_duration_ms": float(act_d),
                    "estimate": {
                        "tokens_median": est_t,
                        "duration_ms_median": est_d,
                        "confidence": "high",
                        "twin_count": 3,
                        "source_twin_ids": [],
                    },
                    "actual": {
                        "tokens": act_t,
                        "duration_ms": act_d,
                        "transition": "spec_finalize_done",
                    },
                },
            )
            _make_trace_comment(t, input_tokens=act_t // 2, output_tokens=act_t // 2)

        # Drive the diagnostic.
        import io
        import contextlib
        from testing_tools import quote_accuracy

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            quote_accuracy.print_report(mode="json")
        report = json.loads(buf.getvalue())

        self.assertEqual(report["row_count"], 3)
        # The diagnostic sorts newest-first; look up by id rather than index.
        by_id = {r["task_id"]: r for r in report["rows"]}
        self.assertEqual(set(by_id.keys()), {1, 2, 3})
        r1 = by_id[1]
        self.assertEqual(r1["estimate_tokens"], 1000)
        self.assertEqual(r1["actual_tokens"], 1200)
        self.assertEqual(r1["estimate_duration_ms"], 60_000)
        self.assertEqual(r1["actual_duration_ms"], 90_000)
        # Aggregate: median absolute %-error (token) is small here.
        self.assertIn("median_token_error_pct", report["aggregate"])
        self.assertIn("median_duration_error_pct", report["aggregate"])

    def test_quote_accuracy_brief_mode_is_short(self):
        """--brief must be 3-8 lines, not a full table."""
        import io
        import contextlib
        from testing_tools import quote_accuracy

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            quote_accuracy.print_report(mode="brief")
        out = buf.getvalue()
        # brief is "3-8 lines" per the shared convention.
        self.assertLessEqual(len([ln for ln in out.splitlines() if ln.strip()]), 8)