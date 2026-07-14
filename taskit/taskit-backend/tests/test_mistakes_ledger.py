"""Tests for the Mistakes Ledger (task #223).

Every rejected review (reflection NEEDS_WORK/FAIL) and failed task is
distilled to one line in a MistakeEntry. New tasks whose twins hit a
mistake carry the warning in the dispatch twins comment.

Scenario matrix:
  Distillation (unit):
   - reflection NEEDS_WORK with summary -> one-liner from summary
   - reflection with empty summary -> graceful fallback one-liner
   - reflection summary mentioning "truncated" -> failure_class=truncation
   - reflection summary mentioning "quota exceeded" -> failure_class=quota_exhaustion
   - code-quality rework (no infra signal) -> failure_class blank
   - execution failure -> one-liner from failure_reason, class from metadata
  Reflection hook (integration):
   - NEEDS_WORK verdict via PATCH /reflections/:id/ creates one entry
   - FAIL verdict creates one entry
   - PASS verdict creates NO entry
   - 3-strike FAILED creates exactly one entry (not two)
   - re-completing the same reflection does not duplicate the entry
  Execution hook (integration):
   - _fail_stale_execution creates an entry (source=execution)
   - re-calling for the same run_token does not duplicate
  Twins warning:
   - a twin task with a mistake surfaces a warning in the twins comment
   - a clean twin surfaces no warning
   - the detail API exposes the warning field
  CLI + spec_trace:
   - mistakes.py --json lists entries
   - spec_trace --json includes a mistake_count
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import MagicMock, patch

from tasks.dag_executor import _fail_stale_execution
from tasks.mistakes import (
    distill_execution,
    distill_reflection,
    record_execution_mistake,
    record_reflection_mistake,
)
from tasks.models import (
    MistakeEntry,
    ReflectionReport,
    ReflectionStatus,
    TaskRun,
    TaskRunState,
    TaskStatus,
)
from tasks.similarity import format_twins_comment, post_twins_comment
from tests.base import APITestCase


# ── Distillation (unit) ────────────────────────────────────────────


def _report(**kwargs):
    """Build an unsaved ReflectionReport-like object for distillation."""
    defaults = dict(
        verdict="NEEDS_WORK",
        verdict_summary="",
        quality_assessment="",
        slop_detection="",
        improvements="",
        quota_failure="",
    )
    defaults.update(kwargs)
    return ReflectionReport(**defaults)


class DistillReflectionTests(APITestCase):
    def test_one_liner_taken_from_summary(self):
        report = _report(
            verdict="NEEDS_WORK",
            verdict_summary="Proof file was truncated before the ODIN-STATUS block.",
        )
        one_liner, _ = distill_reflection(report)
        self.assertIn("truncated", one_liner.lower())

    def test_empty_summary_falls_back_gracefully(self):
        report = _report(verdict="FAIL", verdict_summary="")
        one_liner, _ = distill_reflection(report)
        self.assertTrue(one_liner)
        self.assertIn("FAIL", one_liner)

    def test_truncation_summary_tagged(self):
        report = _report(
            verdict_summary="Output was truncated mid-generation, no status emitted.",
        )
        _, failure_class = distill_reflection(report)
        self.assertEqual(failure_class, "truncation")

    def test_quota_summary_tagged(self):
        report = _report(verdict_summary="Task hit quota exceeded on the provider.")
        _, failure_class = distill_reflection(report)
        self.assertEqual(failure_class, "quota_exhaustion")

    def test_code_quality_rework_has_no_failure_class(self):
        report = _report(verdict_summary="Tests are missing for the new serializer.")
        _, failure_class = distill_reflection(report)
        self.assertEqual(failure_class, "")

    def test_one_liner_is_bounded_in_length(self):
        report = _report(verdict_summary="x " * 500)
        one_liner, _ = distill_reflection(report)
        self.assertLessEqual(len(one_liner), 160)


class DistillExecutionTests(APITestCase):
    def test_one_liner_from_failure_reason(self):
        task = self.make_task(
            self.make_board(),
            status=TaskStatus.FAILED,
            metadata={
                "last_failure_type": "stale_execution",
                "last_failure_reason": "Worker process died mid-run.",
                "failure_class": "stale_execution",
            },
        )
        one_liner, failure_class = distill_execution(task)
        self.assertIn("Worker process died", one_liner)
        self.assertEqual(failure_class, "stale_execution")

    def test_falls_back_to_type_when_no_reason(self):
        task = self.make_task(
            self.make_board(),
            status=TaskStatus.FAILED,
            metadata={"last_failure_type": "timeout", "failure_class": "timeout"},
        )
        one_liner, _ = distill_execution(task)
        self.assertTrue(one_liner)


# ── Reflection hook (integration via PATCH endpoint) ───────────────


class ReflectionMistakeHookTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def _create_report(self, task, status=ReflectionStatus.RUNNING):
        return ReflectionReport.objects.create(
            task=task,
            reviewer_agent="claude",
            reviewer_model="claude-sonnet-4-5",
            requested_by="system@taskit",
            status=status,
        )

    def _complete(self, report_id, verdict, verdict_summary="Summary."):
        return self.client.patch(
            f"/reflections/{report_id}/",
            {"status": "COMPLETED", "verdict": verdict, "verdict_summary": verdict_summary},
            format="json",
        )

    def test_needs_work_creates_entry(self):
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        report = self._create_report(task)
        self._complete(report.id, "NEEDS_WORK", "Tests missing for serializer.")

        entry = MistakeEntry.objects.filter(task=task).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.source, "reflection")
        self.assertEqual(entry.source_id, str(report.id))
        self.assertEqual(entry.verdict, "NEEDS_WORK")
        self.assertIn("Tests missing", entry.one_liner)

    def test_fail_creates_entry(self):
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        report = self._create_report(task)
        self._complete(report.id, "FAIL", "Wrong API shape returned.")
        self.assertTrue(MistakeEntry.objects.filter(task=task, source="reflection").exists())

    def test_pass_creates_no_entry(self):
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        report = self._create_report(task)
        self._complete(report.id, "PASS", "Looks good.")
        self.assertFalse(MistakeEntry.objects.filter(task=task).exists())

    def test_three_strike_failed_creates_one_entry(self):
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        for v in ("NEEDS_WORK", "NEEDS_WORK"):
            ReflectionReport.objects.create(
                task=task, reviewer_agent="claude", reviewer_model="m",
                requested_by="system@taskit", status=ReflectionStatus.COMPLETED,
                verdict=v, verdict_summary="prior round",
            )
        report = self._create_report(task)
        self._complete(report.id, "NEEDS_WORK", "final round")

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)
        # Exactly one entry — the 3-strike FAIL is still a reflection mistake.
        self.assertEqual(MistakeEntry.objects.filter(task=task).count(), 1)

    def test_recompleting_same_reflection_does_not_duplicate(self):
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        report = self._create_report(task)
        record_reflection_mistake(report)
        record_reflection_mistake(report)
        self.assertEqual(
            MistakeEntry.objects.filter(task=task, source_id=str(report.id)).count(), 1,
        )


# ── Execution hook (integration via _fail_stale_execution) ─────────


class ExecutionMistakeHookTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def _executing_task_with_run(self, **kwargs):
        task = self.make_task(
            self.board, status=TaskStatus.EXECUTING, **kwargs
        )
        run = TaskRun.objects.create(
            task=task, spec=task.spec, run_token="tok-stale-1",
            state=TaskRunState.RUNNING,
        )
        return task, run

    def test_stale_failure_creates_entry(self):
        task, run = self._executing_task_with_run()
        _fail_stale_execution(task, reason="dead pid", run_token="tok-stale-1")

        entry = MistakeEntry.objects.filter(task=task, source="execution").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.source_id, "tok-stale-1")
        self.assertEqual(entry.failure_class, "stale_execution")
        self.assertIn("dead pid", entry.one_liner)

    def test_same_run_token_does_not_duplicate(self):
        task, run = self._executing_task_with_run()
        record_execution_mistake(task, run_token="tok-stale-1")
        record_execution_mistake(task, run_token="tok-stale-1")
        self.assertEqual(
            MistakeEntry.objects.filter(task=task, source_id="tok-stale-1").count(), 1,
        )

    def test_different_runs_get_separate_entries(self):
        task, run = self._executing_task_with_run()
        record_execution_mistake(task, run_token="tok-a")
        record_execution_mistake(task, run_token="tok-b")
        self.assertEqual(MistakeEntry.objects.filter(task=task).count(), 2)


# ── Twins warning ──────────────────────────────────────────────────


class TwinsWarningTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def _twin_with_mistake(self):
        twin = self.make_task(
            self.board,
            title="Fix login redirect bug on Safari",
            description="Users on Safari get redirected to /404 after login.",
            status=TaskStatus.FAILED,
            # Real FAILED tasks carry failure_class + reason in metadata,
            # stamped by failure_tagger at the FAILED transition.
            metadata={
                "last_duration_ms": 8000.0,
                "rework_count": 1,
                "last_failure_type": "agent_execution_failure",
                "last_failure_reason": "Output was truncated mid-generation, no status emitted.",
                "failure_class": "truncation",
            },
        )
        record_execution_mistake(twin, run_token="tok-twin")
        return twin

    def test_format_twins_comment_carries_warning(self):
        twin = self._twin_with_mistake()
        twins_payload = [{
            "task_id": twin.id, "title": twin.title, "outcome": TaskStatus.FAILED,
            "tokens": 100, "duration_ms": 8000.0, "redo_rounds": 1, "agent": "—",
            "score": 0.5, "text_score": 0.5, "structural_score": 0.0,
            "proof_path": f".proof/task-{twin.id}/proof.md",
            "warning": {"one_liner": "proof output truncated", "failure_class": "truncation"},
        }]
        body = format_twins_comment(twins_payload)
        self.assertIn("warning", body.lower())
        self.assertIn("proof output truncated", body)

    def test_format_twins_comment_no_warning_for_clean_twin(self):
        twins_payload = [{
            "task_id": 9, "title": "Clean twin", "outcome": TaskStatus.DONE,
            "tokens": 100, "duration_ms": 8000.0, "redo_rounds": 0, "agent": "—",
            "score": 0.5, "text_score": 0.5, "structural_score": 0.0,
            "proof_path": ".proof/task-9/proof.md", "warning": None,
        }]
        body = format_twins_comment(twins_payload)
        self.assertNotIn("warning", body.lower())

    def test_find_twins_surfaces_warning_from_ledger(self):
        twin = self._twin_with_mistake()
        query = self.make_task(
            self.board,
            title="Fix login redirect bug on Firefox",
            description="Users on Firefox get redirected to /404 after login too.",
            status=TaskStatus.IN_PROGRESS,
        )
        from tasks.similarity import find_twins
        twins = find_twins(query)
        self.assertEqual(twins[0]["task_id"], twin.id)
        self.assertIsNotNone(twins[0].get("warning"))
        self.assertIn("truncation", twins[0]["warning"].get("failure_class", ""))

    def test_dispatch_comment_contains_warning(self):
        twin = self._twin_with_mistake()
        task = self.make_task(
            self.board,
            title="Fix login redirect bug on Firefox",
            description="Users on Firefox get redirected to /404 after login too.",
            status=TaskStatus.EXECUTING,
            metadata={"active_execution": {"strategy": "celery_dag", "run_token": "tok-warn"}},
            assignee=self.make_user(),
        )
        post_twins_comment(task)
        from tasks.models import TaskComment
        comment = TaskComment.objects.filter(task=task, author_label="memory").first()
        self.assertIsNotNone(comment)
        self.assertIn("warning", comment.content.lower())

    def test_detail_api_exposes_warning(self):
        twin = self._twin_with_mistake()
        query = self.make_task(
            self.board,
            title="Fix login redirect bug on Firefox",
            description="Users on Firefox get redirected to /404 after login too.",
            status=TaskStatus.IN_PROGRESS,
        )
        resp = self.client.get(f"/tasks/{query.id}/detail/")
        self.assertEqual(resp.status_code, 200)
        twins = resp.data["twins"]
        self.assertEqual(twins[0]["task_id"], twin.id)
        self.assertIsNotNone(twins[0].get("warning"))


# ── CLI + spec_trace ───────────────────────────────────────────────


class MistakesCLIAndTraceTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board, odin_id="sp_m", title="M spec")

    def test_mistakes_cli_json_lists_entries(self):
        task = self.make_task(self.board, spec=self.spec, status=TaskStatus.FAILED)
        record_execution_mistake(
            task, run_token="tok-cli",
        )
        from testing_tools.mistakes import list_mistakes
        data = list_mistakes(mode="json", board_id=self.board.id)
        self.assertEqual(data["board_id"], self.board.id)
        self.assertGreaterEqual(data["count"], 1)
        ids = [e["task_id"] for e in data["mistakes"]]
        self.assertIn(task.id, ids)

    def test_spec_trace_json_includes_mistake_count(self):
        task = self.make_task(self.board, spec=self.spec, status=TaskStatus.FAILED)
        record_execution_mistake(task, run_token="tok-trace")
        from testing_tools.spec_trace import trace_spec
        import io
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            trace_spec(self.spec.id, mode="json")
        import json
        data = json.loads(buf.getvalue())
        self.assertIn("mistake_count", data)
        self.assertGreaterEqual(data["mistake_count"], 1)
