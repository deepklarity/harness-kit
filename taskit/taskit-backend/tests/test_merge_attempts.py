"""Tests for MergeAttempt records + spec merge rollup (task #209).

The merge ladder is: static git merge first (cheap, no model) -> merge
agent only on conflict (model/tokens/cost) -> human only when the agent
can't resolve. Every rung must leave a MergeAttempt row, and a spec's
merge story must roll up (spec_trace.py + the spec detail/diagnostic API)
exactly like reflection reports already do.

Four layers:

1. ``record_merge_attempt`` (tasks/merge_recording.py) — the recording
   helper shared by every dispatch point.
2. ``compute_spec_merge_summary`` (tasks/pricing.py) — the rollup used by
   spec_trace.py and the serializers.
3. Wiring: ``merge_task_on_reflection`` / ``resume_merge_with_guidance``
   (tasks/dag_executor.py) actually write a row when a merge completes.
4. API: spec detail/diagnostic responses expose ``merge_summary``.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from odin.worktree import MergeResult

from tasks.dag_executor import merge_task_on_reflection, resume_merge_with_guidance
from tasks.merge_recording import record_merge_attempt
from tasks.models import (
    CommentType,
    MergeAttempt,
    MergeMode,
    MergeOutcome,
    MergeTrigger,
    Task,
    TaskComment,
    TaskStatus,
)
from tasks.pricing import compute_spec_merge_summary
from tests.base import APITestCase


def _clear_pricing_cache():
    from tasks.pricing import get_pricing_table
    get_pricing_table.cache_clear()


class RecordMergeAttemptTests(APITestCase):
    """Unit tests for the recording helper itself."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board, odin_id="sp_209")
        self.task = self.make_task(self.board, spec=self.spec)

    def test_static_merge_writes_record_with_no_cost(self):
        """A plain git merge (no conflict) records mode=static, no cost fields."""
        merge_result = MergeResult(success=True, diff_stat=" 1 file changed")
        started_at = timezone.now()
        finished_at = started_at + timedelta(seconds=2)

        attempt = record_merge_attempt(
            self.task, MergeTrigger.REFLECTION_PASS, merge_result, started_at, finished_at,
        )

        self.assertEqual(attempt.mode, MergeMode.STATIC)
        self.assertEqual(attempt.outcome, MergeOutcome.MERGED)
        self.assertEqual(attempt.trigger, MergeTrigger.REFLECTION_PASS)
        self.assertEqual(attempt.agent_model, "")
        self.assertEqual(attempt.token_usage, {})
        self.assertEqual(attempt.task_id, self.task.id)
        self.assertEqual(attempt.spec_id, self.spec.id)

    def test_agent_merge_writes_model_tokens(self):
        """A mechanical-conflict resolution records mode=agent with model+tokens."""
        merge_result = MergeResult(
            success=True,
            resolved_files=["opencode.json"],
            resolution_rationale="Generated config — safe to take spec side.",
            agent_model="claude-sonnet-5",
            token_usage={"input_tokens": 1000, "output_tokens": 500},
        )
        started_at = timezone.now()
        finished_at = started_at + timedelta(seconds=5)

        attempt = record_merge_attempt(
            self.task, MergeTrigger.REFLECTION_PASS, merge_result, started_at, finished_at,
        )

        self.assertEqual(attempt.mode, MergeMode.AGENT)
        self.assertEqual(attempt.outcome, MergeOutcome.MERGED)
        self.assertEqual(attempt.agent_model, "claude-sonnet-5")
        self.assertEqual(attempt.token_usage, {"input_tokens": 1000, "output_tokens": 500})

    def test_conflict_outcome_records_conflicting_files(self):
        merge_result = MergeResult(
            success=False, conflict=True, needs_human=True,
            conflicting_files=["a.py", "b.py"],
            ambiguous_files=["a.py", "b.py"],
            error="Merge conflict (needs human): ambiguous",
        )
        started_at = timezone.now()
        finished_at = started_at + timedelta(seconds=1)

        attempt = record_merge_attempt(
            self.task, MergeTrigger.REFLECTION_PASS, merge_result, started_at, finished_at,
        )

        self.assertEqual(attempt.mode, MergeMode.AGENT)
        self.assertEqual(attempt.outcome, MergeOutcome.CONFLICT)
        self.assertEqual(attempt.conflicting_files, ["a.py", "b.py"])
        self.assertIn("ambiguous", attempt.error_message)

    def test_error_outcome_when_not_success_not_conflict(self):
        merge_result = MergeResult(success=False, error="WorktreeManager not available")

        attempt = record_merge_attempt(
            self.task, MergeTrigger.REFLECTION_PASS, merge_result, timezone.now(), timezone.now(),
        )

        self.assertEqual(attempt.outcome, MergeOutcome.ERROR)

    def test_human_resume_trigger_forces_human_assisted_mode(self):
        """Even a resolved-by-guidance attempt (no resolved_files) is human_assisted."""
        merge_result = MergeResult(success=True)

        attempt = record_merge_attempt(
            self.task, MergeTrigger.HUMAN_RESUME, merge_result, timezone.now(), timezone.now(),
        )

        self.assertEqual(attempt.mode, MergeMode.HUMAN_ASSISTED)

    def test_dispatch_lag_captured(self):
        dispatched_at = timezone.now()
        started_at = dispatched_at + timedelta(seconds=7)
        finished_at = started_at + timedelta(seconds=1)
        merge_result = MergeResult(success=True)

        attempt = record_merge_attempt(
            self.task, MergeTrigger.REFLECTION_PASS, merge_result, started_at, finished_at,
            dispatched_at=dispatched_at,
        )

        self.assertEqual(attempt.dispatched_at, dispatched_at)
        self.assertEqual((attempt.started_at - attempt.dispatched_at).total_seconds(), 7)

    def test_dispatch_lag_optional(self):
        """No dispatched_at (e.g. unparseable stamp) records None, not a crash."""
        merge_result = MergeResult(success=True)
        attempt = record_merge_attempt(
            self.task, MergeTrigger.REFLECTION_PASS, merge_result, timezone.now(), timezone.now(),
        )
        self.assertIsNone(attempt.dispatched_at)


class SpecMergeSummaryTests(APITestCase):
    """compute_spec_merge_summary rollup correctness."""

    def setUp(self):
        super().setUp()
        _clear_pricing_cache()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board, odin_id="sp_209b")

    def test_no_tasks_returns_zeroed_summary(self):
        summary = compute_spec_merge_summary([])
        self.assertEqual(summary["attempt_count"], 0)
        self.assertEqual(summary["merge_cost_usd"], 0)
        self.assertIsNone(summary["mean_dispatch_lag_seconds"])
        self.assertEqual(summary["conflicts_by_file"], {})

    def test_rollup_sums_static_and_agent_attempts(self):
        t1 = self.make_task(self.board, spec=self.spec, title="T1")
        t2 = self.make_task(self.board, spec=self.spec, title="T2")

        # Static merge, no cost
        static_result = MergeResult(success=True)
        record_merge_attempt(t1, MergeTrigger.REFLECTION_PASS, static_result, timezone.now(), timezone.now())

        # Agent merge, with cost
        agent_result = MergeResult(
            success=True, resolved_files=["opencode.json"],
            agent_model="claude-sonnet-5",
            token_usage={"input_tokens": 1000, "output_tokens": 500},
        )
        record_merge_attempt(t2, MergeTrigger.REFLECTION_PASS, agent_result, timezone.now(), timezone.now())

        summary = compute_spec_merge_summary([t1, t2])
        self.assertEqual(summary["attempt_count"], 2)
        self.assertEqual(summary["static_count"], 1)
        self.assertEqual(summary["agent_count"], 1)
        self.assertEqual(summary["human_assisted_count"], 0)
        # (1000/1M)*3.00 + (500/1M)*15.00 = 0.0105
        self.assertAlmostEqual(summary["merge_cost_usd"], 0.0105, places=6)

    def test_rollup_mean_dispatch_lag(self):
        t1 = self.make_task(self.board, spec=self.spec, title="T1")
        t2 = self.make_task(self.board, spec=self.spec, title="T2")
        now = timezone.now()

        record_merge_attempt(
            t1, MergeTrigger.REFLECTION_PASS, MergeResult(success=True),
            now + timedelta(seconds=4), now + timedelta(seconds=5), dispatched_at=now,
        )
        record_merge_attempt(
            t2, MergeTrigger.REFLECTION_PASS, MergeResult(success=True),
            now + timedelta(seconds=10), now + timedelta(seconds=11), dispatched_at=now,
        )

        summary = compute_spec_merge_summary([t1, t2])
        self.assertAlmostEqual(summary["mean_dispatch_lag_seconds"], 7.0, places=3)

    def test_rollup_conflicts_by_file(self):
        t1 = self.make_task(self.board, spec=self.spec, title="T1")
        t2 = self.make_task(self.board, spec=self.spec, title="T2")

        record_merge_attempt(
            t1, MergeTrigger.REFLECTION_PASS,
            MergeResult(success=False, conflict=True, conflicting_files=["a.py", "b.py"]),
            timezone.now(), timezone.now(),
        )
        record_merge_attempt(
            t2, MergeTrigger.REFLECTION_PASS,
            MergeResult(success=False, conflict=True, conflicting_files=["a.py"]),
            timezone.now(), timezone.now(),
        )

        summary = compute_spec_merge_summary([t1, t2])
        self.assertEqual(summary["conflicts_by_file"], {"a.py": 2, "b.py": 1})


class MergeTaskOnReflectionWiringTests(APITestCase):
    """merge_task_on_reflection writes a MergeAttempt row."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def _task_in_review(self, extra_metadata=None):
        spec = self.make_spec(self.board, odin_id="sp_wire", metadata={"branch": "spec/sp_wire"})
        metadata = {"branch": "task/sp_wire/1"}
        metadata.update(extra_metadata or {})
        return self.make_task(self.board, spec=spec, status=TaskStatus.REVIEW, metadata=metadata)

    @patch("tasks.dag_executor._advance_task_to_testing")
    @patch("tasks.dag_executor._merge_task_branch")
    def test_static_merge_records_reflection_pass_trigger(self, mock_merge_branch, _mock_advance):
        task = self._task_in_review()
        mock_merge_branch.return_value = MergeResult(success=True, diff_stat=" 1 file changed")

        merge_task_on_reflection.__wrapped__(task.id)

        attempt = MergeAttempt.objects.get(task=task)
        self.assertEqual(attempt.trigger, MergeTrigger.REFLECTION_PASS)
        self.assertEqual(attempt.mode, MergeMode.STATIC)
        self.assertEqual(attempt.outcome, MergeOutcome.MERGED)

    @patch("tasks.dag_executor._advance_task_to_testing")
    @patch("tasks.dag_executor._merge_task_branch")
    def test_watchdog_retry_records_retry_trigger(self, mock_merge_branch, _mock_advance):
        task = self._task_in_review({"merge_dispatch_source": "watchdog"})
        mock_merge_branch.return_value = MergeResult(success=True)

        merge_task_on_reflection.__wrapped__(task.id)

        attempt = MergeAttempt.objects.get(task=task)
        self.assertEqual(attempt.trigger, MergeTrigger.RETRY)

    @patch("tasks.dag_executor._advance_task_to_testing")
    @patch("tasks.dag_executor._merge_task_branch")
    def test_dispatch_lag_captured_from_metadata_stamp(self, mock_merge_branch, _mock_advance):
        dispatched_at = timezone.now() - timedelta(seconds=30)
        task = self._task_in_review({"merge_dispatched_at": dispatched_at.isoformat()})
        mock_merge_branch.return_value = MergeResult(success=True)

        merge_task_on_reflection.__wrapped__(task.id)

        attempt = MergeAttempt.objects.get(task=task)
        self.assertIsNotNone(attempt.dispatched_at)
        self.assertGreater((attempt.started_at - attempt.dispatched_at).total_seconds(), 0)

    @patch("tasks.dag_executor._advance_task_to_testing")
    @patch("tasks.dag_executor._merge_task_branch")
    def test_agent_resolved_merge_records_agent_mode_and_cost(self, mock_merge_branch, _mock_advance):
        _clear_pricing_cache()
        task = self._task_in_review()
        mock_merge_branch.return_value = MergeResult(
            success=True,
            resolved_files=["opencode.json"],
            resolution_rationale="Generated configs — safe to take spec side.",
            agent_model="claude-sonnet-5",
            token_usage={"input_tokens": 2000, "output_tokens": 800},
        )

        merge_task_on_reflection.__wrapped__(task.id)

        attempt = MergeAttempt.objects.get(task=task)
        self.assertEqual(attempt.mode, MergeMode.AGENT)
        self.assertEqual(attempt.agent_model, "claude-sonnet-5")
        self.assertEqual(attempt.token_usage, {"input_tokens": 2000, "output_tokens": 800})


class ResumeMergeWiringTests(APITestCase):
    """resume_merge_with_guidance writes a human_resume MergeAttempt row."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def _task_needs_human(self):
        spec = self.make_spec(self.board, odin_id="sp_resume", metadata={"branch": "spec/sp_resume"})
        return self.make_task(
            self.board, spec=spec, status=TaskStatus.REVIEW,
            metadata={"branch": "task/sp_resume/1", "merge_status": "needs_human"},
        )

    @patch("tasks.dag_executor._advance_task_to_testing")
    @patch("tasks.dag_executor._merge_task_branch")
    def test_resume_records_human_resume_trigger_and_dispatch_lag(self, mock_merge_branch, _mock_advance):
        task = self._task_needs_human()
        comment = TaskComment.objects.create(
            task=task, author_email="alice@test.com",
            content="keep the task side", comment_type=CommentType.REPLY,
        )
        mock_merge_branch.return_value = MergeResult(success=True, resolved_files=["a.py"])

        resume_merge_with_guidance.__wrapped__(task.id, comment.id)

        attempt = MergeAttempt.objects.get(task=task)
        self.assertEqual(attempt.trigger, MergeTrigger.HUMAN_RESUME)
        self.assertEqual(attempt.mode, MergeMode.HUMAN_ASSISTED)
        self.assertEqual(attempt.dispatched_at, comment.created_at)


class SpecMergeSummaryAPITests(APITestCase):
    """The spec detail/diagnostic API exposes merge_summary."""

    def setUp(self):
        super().setUp()
        _clear_pricing_cache()
        self.board = self.make_board()

    def test_spec_detail_includes_merge_summary(self):
        spec = self.make_spec(self.board, odin_id="sp_api")
        task = self.make_task(self.board, spec=spec, title="T1")
        record_merge_attempt(
            task, MergeTrigger.REFLECTION_PASS,
            MergeResult(
                success=True, resolved_files=["x"],
                agent_model="claude-sonnet-5",
                token_usage={"input_tokens": 1000, "output_tokens": 500},
            ),
            timezone.now(), timezone.now(),
        )

        resp = self.client.get(f"/specs/{spec.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("merge_summary", resp.data)
        summary = resp.data["merge_summary"]
        self.assertEqual(summary["attempt_count"], 1)
        self.assertEqual(summary["agent_count"], 1)
        self.assertAlmostEqual(summary["merge_cost_usd"], 0.0105, places=6)

    def test_spec_diagnostic_includes_merge_summary(self):
        spec = self.make_spec(self.board, odin_id="sp_api2")
        task = self.make_task(self.board, spec=spec, title="T1")
        record_merge_attempt(
            task, MergeTrigger.REFLECTION_PASS, MergeResult(success=True),
            timezone.now(), timezone.now(),
        )

        resp = self.client.get(f"/specs/{spec.id}/diagnostic/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("merge_summary", resp.data)
        self.assertEqual(resp.data["merge_summary"]["static_count"], 1)

    def test_spec_detail_no_merge_attempts_zeroed(self):
        spec = self.make_spec(self.board, odin_id="sp_api3")
        self.make_task(self.board, spec=spec, title="T1")

        resp = self.client.get(f"/specs/{spec.id}/")
        summary = resp.data["merge_summary"]
        self.assertEqual(summary["attempt_count"], 0)
        self.assertEqual(summary["merge_cost_usd"], 0)
