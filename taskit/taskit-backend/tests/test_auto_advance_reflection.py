"""Tests for auto-advancing tasks based on reflection verdicts.

PASS → REVIEW → TESTING (existing)
NEEDS_WORK → REVIEW → IN_PROGRESS (retry) or REVIEW → FAILED (after 3 attempts)
FAIL → stays in REVIEW (human triage)
Quota failure → reassign to different agent before retry
"""

from unittest.mock import MagicMock, patch

from django.test import override_settings

from .base import APITestCase
from tasks import sandbox_budget
from tasks.models import (
    BoardMembership, ReflectionReport, ReflectionStatus,
    TaskComment, TaskHistory, TaskStatus, User, UserRole,
)

VM = sandbox_budget.DEFAULT_VM_MEM_MIB


class TestAutoAdvanceOnReflection(APITestCase):
    """PATCH /reflections/:id/ auto-advances task based on verdict."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def _create_report(self, task, status=ReflectionStatus.RUNNING):
        return ReflectionReport.objects.create(
            task=task,
            reviewer_agent="claude",
            reviewer_model="claude-sonnet-4-5-20250929",
            requested_by="system@taskit",
            status=status,
        )

    def _create_completed_report(self, task, verdict="NEEDS_WORK"):
        """Create a report already in COMPLETED state (counts toward limit)."""
        return ReflectionReport.objects.create(
            task=task,
            reviewer_agent="claude",
            reviewer_model="claude-sonnet-4-5-20250929",
            requested_by="system@taskit",
            status=ReflectionStatus.COMPLETED,
            verdict=verdict,
            verdict_summary="Previous attempt.",
        )

    def _complete_report(self, report_id, verdict, verdict_summary="Summary."):
        return self.client.patch(
            f"/reflections/{report_id}/",
            {
                "status": "COMPLETED",
                "verdict": verdict,
                "verdict_summary": verdict_summary,
            },
            format="json",
        )

    # ── PASS → auto-advance ──────────────────────────────────────

    def test_pass_verdict_moves_task_from_review_to_testing(self):
        """Reflection PASS should advance task REVIEW → TESTING."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        report = self._create_report(task)

        resp = self._complete_report(report.id, verdict="PASS")
        self.assertEqual(resp.status_code, 200)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.TESTING)

    def test_pass_verdict_records_task_history(self):
        """Auto-advance should create a TaskHistory entry."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        report = self._create_report(task)

        self._complete_report(report.id, verdict="PASS")

        history = TaskHistory.objects.filter(
            task=task, field_name="status"
        ).order_by("-changed_at").first()
        self.assertIsNotNone(history)
        self.assertEqual(history.old_value, TaskStatus.REVIEW)
        self.assertEqual(history.new_value, TaskStatus.TESTING)
        self.assertEqual(history.changed_by, "system@taskit")

    # ── NEEDS_WORK → retry loop ──────────────────────────────────

    def test_needs_work_moves_task_from_review_to_in_progress(self):
        """First NEEDS_WORK should send task back to IN_PROGRESS for retry."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        report = self._create_report(task)

        resp = self._complete_report(report.id, verdict="NEEDS_WORK")
        self.assertEqual(resp.status_code, 200)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)

    def test_needs_work_records_task_history(self):
        """NEEDS_WORK retry should create a TaskHistory entry."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        report = self._create_report(task)

        self._complete_report(report.id, verdict="NEEDS_WORK")

        history = TaskHistory.objects.filter(
            task=task, field_name="status"
        ).order_by("-changed_at").first()
        self.assertIsNotNone(history)
        self.assertEqual(history.old_value, TaskStatus.REVIEW)
        self.assertEqual(history.new_value, TaskStatus.IN_PROGRESS)
        self.assertEqual(history.changed_by, "system@taskit")

    @patch("tasks.execution.get_strategy")
    def test_needs_work_triggers_execution_strategy(self, mock_get_strategy):
        """NEEDS_WORK should fire the execution strategy for assigned tasks."""
        user = User.objects.create(name="Agent", email="agent@test.com")
        task = self.make_task(self.board, status=TaskStatus.REVIEW, assignee=user)
        report = self._create_report(task)

        mock_strategy = MagicMock()
        mock_get_strategy.return_value = mock_strategy

        self._complete_report(report.id, verdict="NEEDS_WORK")

        mock_strategy.trigger.assert_called_once_with(task)

    # ── Concurrency cap on rework dispatch ───────────────────────

    @patch("tasks.execution.get_strategy")
    @override_settings(DAG_EXECUTOR_MAX_CONCURRENCY=2)
    def test_needs_work_holds_queued_at_concurrency_cap(self, mock_get_strategy):
        """At cap, rework queues instead of firing the execution trigger.

        Regression: reflection-ordered rework called strategy.trigger()
        unconditionally, bypassing DAG_EXECUTOR_MAX_CONCURRENCY and letting
        rework re-enter EXECUTING past the cap. Now the rework path counts
        EXECUTING tasks and holds the task IN_PROGRESS (queued) when the cap
        is reached — poll_and_execute dispatches it when a slot frees.
        """
        user = User.objects.create(name="Agent", email="agent@cap.test")
        self.make_task(self.board, title="Exec 1", status=TaskStatus.EXECUTING)
        self.make_task(self.board, title="Exec 2", status=TaskStatus.EXECUTING)
        task = self.make_task(self.board, status=TaskStatus.REVIEW, assignee=user)
        report = self._create_report(task)

        mock_strategy = MagicMock()
        mock_get_strategy.return_value = mock_strategy

        self._complete_report(report.id, verdict="NEEDS_WORK")

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        mock_strategy.trigger.assert_not_called()
        self.assertEqual(
            task.metadata.get("dispatch_blocked_reason"),
            "concurrency_cap_reached",
        )

    @patch("tasks.execution.get_strategy")
    @override_settings(DAG_EXECUTOR_MAX_CONCURRENCY=2)
    def test_needs_work_fires_trigger_below_cap(self, mock_get_strategy):
        """Below cap, rework still fires the execution trigger (unchanged)."""
        user = User.objects.create(name="Agent", email="agent@room.test")
        self.make_task(self.board, title="Exec 1", status=TaskStatus.EXECUTING)
        task = self.make_task(self.board, status=TaskStatus.REVIEW, assignee=user)
        report = self._create_report(task)

        mock_strategy = MagicMock()
        mock_get_strategy.return_value = mock_strategy

        self._complete_report(report.id, verdict="NEEDS_WORK")

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        mock_strategy.trigger.assert_called_once_with(task)
        self.assertNotIn("dispatch_blocked_reason", task.metadata or {})

    # ── Task #353: requeue must clear stale dispatch_blocked_reason ──────

    @patch("tasks.execution.get_strategy")
    def test_needs_work_rework_clears_stale_dispatch_blocked_reason(self, mock_get_strategy):
        """NEEDS_WORK → strategy.trigger(): the dispatch_blocked_reason stamp
        left by an earlier gate must NOT survive the requeue (otherwise the
        banner lies about a task that's already executing).

        Reproduces the feedback from the prior review round: a reflective
        verdict that triggers the strategy leaves the banner in place even
        though the task has been re-spawned. The fix is to clear the stamp
        before the gate assessment; if the gate still says "no", the stamp
        is re-set with the fresh reason.
        """
        user = User.objects.create(name="Agent", email="agent@stale.test")
        # A stale stamp from an earlier round (the bug under test).
        task = self.make_task(
            self.board, status=TaskStatus.REVIEW, assignee=user,
            metadata={
                "dispatch_blocked_reason": "concurrency_cap_reached",
                "dispatch_blocked_at": "2026-07-01T00:00:00Z",
            },
        )
        report = self._create_report(task)

        mock_strategy = MagicMock()
        mock_get_strategy.return_value = mock_strategy

        self._complete_report(report.id, verdict="NEEDS_WORK")

        # Strategy fires (rework below cap → no gate hold).
        mock_strategy.trigger.assert_called_once_with(task)
        # Stamp must be gone — the task is on its way to EXECUTING.
        task.refresh_from_db()
        self.assertNotIn("dispatch_blocked_reason", task.metadata or {})
        self.assertNotIn("dispatch_blocked_at", task.metadata or {})

    @patch("tasks.execution.get_strategy")
    def test_fail_verdict_rework_clears_stale_dispatch_blocked_reason(self, mock_get_strategy):
        """FAIL verdict follows the same requeue path as NEEDS_WORK — the
        stamp must clear when strategy.trigger() fires."""
        user = User.objects.create(name="Agent", email="agent@fail-cleared.test")
        task = self.make_task(
            self.board, status=TaskStatus.REVIEW, assignee=user,
            metadata={
                "dispatch_blocked_reason": "memory_budget_full",
                "dispatch_blocked_at": "2026-07-02T00:00:00Z",
            },
        )
        report = self._create_report(task)

        mock_strategy = MagicMock()
        mock_get_strategy.return_value = mock_strategy

        self._complete_report(report.id, verdict="FAIL")

        task.refresh_from_db()
        self.assertNotIn("dispatch_blocked_reason", task.metadata or {})

    @patch("tasks.execution.get_strategy")
    @override_settings(SANDBOX_MEMORY_BUDGET_MIB=VM, DAG_EXECUTOR_MAX_CONCURRENCY=10)
    def test_rework_memory_full_stamp_names_holder_tasks(self, mock_get_strategy):
        """When the rework gate holds at memory_budget_full, the stamp must
        name the holder tasks (same contract as the poll path) so the banner
        can show "waiting for a memory share — held by task X, reflection on Y".
        """
        from tasks import sandbox_budget

        user = User.objects.create(name="Agent", email="agent@stamp.test")
        holder = self.make_task(
            self.board, title="Live exec", status=TaskStatus.EXECUTING,
            metadata={"active_execution": {"mem_mib": VM}},
        )
        reflected = self.make_task(self.board, title="Refl holder",
                                   status=TaskStatus.REVIEW)
        ReflectionReport.objects.create(
            task=reflected, reviewer_agent="claude", reviewer_model="m",
            status=ReflectionStatus.RUNNING,
        )
        task = self.make_task(self.board, status=TaskStatus.REVIEW, assignee=user)
        report = self._create_report(task)

        mock_strategy = MagicMock()
        mock_get_strategy.return_value = mock_strategy

        self._complete_report(report.id, verdict="NEEDS_WORK")

        task.refresh_from_db()
        # Gate held the task → stamp is set with holder names.
        self.assertEqual(task.metadata.get("dispatch_blocked_reason"),
                         "memory_budget_full")
        blocked_by = task.metadata.get("dispatch_blocked_blocked_by") or []
        ids = {h["task_id"] for h in blocked_by}
        self.assertIn(holder.id, ids)
        self.assertIn(reflected.id, ids)
        # Strategy did NOT fire (gate held).
        mock_strategy.trigger.assert_not_called()

    # ── 3-strike failure ─────────────────────────────────────────

    def test_third_needs_work_fails_task(self):
        """After 3 completed reflections, NEEDS_WORK should FAIL the task."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        # Two prior completed reflections
        self._create_completed_report(task, verdict="NEEDS_WORK")
        self._create_completed_report(task, verdict="NEEDS_WORK")
        # Third attempt (this one completes via the API, making count = 3)
        report = self._create_report(task)

        self._complete_report(report.id, verdict="NEEDS_WORK")

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)

    def test_third_needs_work_posts_failure_comment(self):
        """Failure after 3 attempts should post an explanatory comment."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        self._create_completed_report(task, verdict="NEEDS_WORK")
        self._create_completed_report(task, verdict="NEEDS_WORK")
        report = self._create_report(task)

        self._complete_report(report.id, verdict="NEEDS_WORK")

        comment = TaskComment.objects.filter(task=task, author_email="system@taskit").last()
        self.assertIsNotNone(comment)
        # task #359 — the comment leads with the cause and the next step,
        # not the log-style "3 reflection attempts without passing" line.
        self.assertIn("review cap", comment.content.lower())
        self.assertIn("reviewer", comment.content.lower())

    def test_mixed_verdicts_count_toward_limit(self):
        """Any completed reflection counts — not just NEEDS_WORK verdicts."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        # Two prior: one NEEDS_WORK + one FAIL = 2 completed
        self._create_completed_report(task, verdict="NEEDS_WORK")
        self._create_completed_report(task, verdict="FAIL")
        # Third attempt via API (count becomes 3)
        report = self._create_report(task)

        self._complete_report(report.id, verdict="NEEDS_WORK")

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)

    # ── FAIL verdict → same retry loop as NEEDS_WORK ────────────

    def test_fail_verdict_moves_task_to_in_progress(self):
        """Reflection FAIL should send task back for retry (same as NEEDS_WORK)."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        report = self._create_report(task)

        self._complete_report(report.id, verdict="FAIL")

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)

    def test_third_fail_verdict_fails_task(self):
        """After 3 completed reflections, FAIL should FAIL the task."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        self._create_completed_report(task, verdict="FAIL")
        self._create_completed_report(task, verdict="FAIL")
        report = self._create_report(task)

        self._complete_report(report.id, verdict="FAIL")

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)

    # ── Guard against concurrent status changes ──────────────────

    def test_pass_verdict_does_not_overwrite_done_status(self):
        """If task already moved to DONE, PASS should not regress it."""
        task = self.make_task(self.board, status=TaskStatus.DONE)
        report = self._create_report(task)

        self._complete_report(report.id, verdict="PASS")

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.DONE)

    def test_pass_verdict_does_not_overwrite_testing_status(self):
        """If task is already in TESTING, no duplicate transition."""
        task = self.make_task(self.board, status=TaskStatus.TESTING)
        report = self._create_report(task)

        self._complete_report(report.id, verdict="PASS")

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.TESTING)
        # No new history entry since status didn't change
        history_count = TaskHistory.objects.filter(
            task=task, field_name="status", changed_by="system@taskit"
        ).count()
        self.assertEqual(history_count, 0)

    def test_needs_work_does_not_overwrite_non_review_status(self):
        """If task already moved out of REVIEW, NEEDS_WORK should not overwrite."""
        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        report = self._create_report(task)

        self._complete_report(report.id, verdict="NEEDS_WORK")

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        # No history entry since guard prevented the transition
        history_count = TaskHistory.objects.filter(
            task=task, field_name="status", changed_by="system@taskit"
        ).count()
        self.assertEqual(history_count, 0)


class TestQuotaFailureReassignment(APITestCase):
    """Quota/rate-limit failures should reassign to a different agent before retry."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        # Create two agent users
        self.claude_agent = User.objects.create(
            name="claude", email="claude@odin.agent", role=UserRole.AGENT,
            available_models=["claude-sonnet-4-5-20250929"],
        )
        self.glm_agent = User.objects.create(
            name="glm", email="glm@odin.agent", role=UserRole.AGENT,
            available_models=["zai-coding-plan/glm-5.2"],
        )
        # Add both to the board
        BoardMembership.objects.create(board=self.board, user=self.claude_agent)
        BoardMembership.objects.create(board=self.board, user=self.glm_agent)
        # Task #159: quota reassignment now requires ground-truth verification.
        # Mock the provider as genuinely exhausted so these tests continue to
        # pin the *verified* reassignment path rather than the checker-down
        # fallback. Non-quota tests below never reach the checker.
        usage_patch = patch("tasks.views._get_usage_from_provider")
        self.mock_usage = usage_patch.start()
        self.addCleanup(usage_patch.stop)
        self.mock_usage.return_value = (98.0, None)

    def _create_report(self, task, status=ReflectionStatus.RUNNING, **kwargs):
        return ReflectionReport.objects.create(
            task=task,
            reviewer_agent="claude",
            reviewer_model="claude-sonnet-4-5-20250929",
            requested_by="system@taskit",
            status=status,
            **kwargs,
        )

    def _complete_report(self, report_id, verdict, verdict_summary="Summary.", **extra):
        payload = {
            "status": "COMPLETED",
            "verdict": verdict,
            "verdict_summary": verdict_summary,
            **extra,
        }
        return self.client.patch(
            f"/reflections/{report_id}/",
            payload,
            format="json",
        )

    # ── Quota detection from task metadata ────────────────────────

    @patch("tasks.execution.get_strategy")
    def test_quota_failure_from_metadata_triggers_reassignment(self, mock_get_strategy):
        """Task with last_failure_type=llm_call_failure + quota keyword → reassign."""
        mock_get_strategy.return_value = MagicMock()

        task = self.make_task(
            self.board,
            status=TaskStatus.REVIEW,
            assignee=self.claude_agent,
            model_name="claude-sonnet-4-5-20250929",
            metadata={
                "last_failure_type": "llm_call_failure",
                "last_failure_reason": "HTTP 429: rate limit exceeded",
            },
        )
        report = self._create_report(task)
        self._complete_report(report.id, verdict="FAIL", verdict_summary="Rate limit hit.")

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        self.assertEqual(task.assignee_id, self.glm_agent.id)
        self.assertEqual(task.model_name, "zai-coding-plan/glm-5.2")

    # ── Quota detection from reflection quota_failure field ───────

    @patch("tasks.execution.get_strategy")
    def test_quota_failure_from_reflection_field_triggers_reassignment(self, mock_get_strategy):
        """Reflection quota_failure field flagged → reassign."""
        mock_get_strategy.return_value = MagicMock()

        task = self.make_task(
            self.board,
            status=TaskStatus.REVIEW,
            assignee=self.claude_agent,
            model_name="claude-sonnet-4-5-20250929",
        )
        report = self._create_report(task)
        self._complete_report(
            report.id,
            verdict="FAIL",
            verdict_summary="Agent could not complete.",
            quota_failure="QUOTA_FAILURE: claude",
        )

        task.refresh_from_db()
        self.assertEqual(task.assignee_id, self.glm_agent.id)

    # ── Quota detection from verdict_summary keywords ────────────

    @patch("tasks.execution.get_strategy")
    def test_quota_keyword_in_verdict_summary_triggers_reassignment(self, mock_get_strategy):
        """Verdict summary mentioning 'quota exceeded' → reassign."""
        mock_get_strategy.return_value = MagicMock()

        task = self.make_task(
            self.board,
            status=TaskStatus.REVIEW,
            assignee=self.claude_agent,
            model_name="claude-sonnet-4-5-20250929",
        )
        report = self._create_report(task)
        self._complete_report(
            report.id,
            verdict="FAIL",
            verdict_summary="Task failed because quota exceeded on Claude.",
        )

        task.refresh_from_db()
        self.assertEqual(task.assignee_id, self.glm_agent.id)
        self.assertEqual(task.model_name, "zai-coding-plan/glm-5.2")

    # ── History and comment recording ────────────────────────────

    @patch("tasks.execution.get_strategy")
    def test_reassignment_records_history_and_comment(self, mock_get_strategy):
        """Reassignment should create TaskHistory entries and a comment."""
        mock_get_strategy.return_value = MagicMock()

        task = self.make_task(
            self.board,
            status=TaskStatus.REVIEW,
            assignee=self.claude_agent,
            model_name="claude-sonnet-4-5-20250929",
            metadata={
                "last_failure_type": "llm_call_failure",
                "last_failure_reason": "quota exceeded",
            },
        )
        report = self._create_report(task)
        self._complete_report(report.id, verdict="FAIL")

        # Assignee history
        assignee_history = TaskHistory.objects.filter(
            task=task, field_name="assignee", changed_by="system@taskit",
        ).first()
        self.assertIsNotNone(assignee_history)
        self.assertEqual(assignee_history.old_value, "claude")
        self.assertEqual(assignee_history.new_value, "glm")

        # Model history
        model_history = TaskHistory.objects.filter(
            task=task, field_name="model", changed_by="system@taskit",
        ).first()
        self.assertIsNotNone(model_history)

        # Comment
        comment = TaskComment.objects.filter(
            task=task, author_email="system@taskit",
        ).order_by("-created_at").first()
        self.assertIn("Quota/rate-limit failure", comment.content)
        self.assertIn("glm", comment.content)

    # ── No reassignment when no alternative agent ────────────────

    @patch("tasks.execution.get_strategy")
    def test_no_reassignment_when_no_alternative_agent(self, mock_get_strategy):
        """If only one agent exists, skip reassignment but still retry."""
        mock_get_strategy.return_value = MagicMock()

        # Remove the alternative agent from the board
        BoardMembership.objects.filter(user=self.glm_agent).delete()
        self.glm_agent.delete()

        task = self.make_task(
            self.board,
            status=TaskStatus.REVIEW,
            assignee=self.claude_agent,
            model_name="claude-sonnet-4-5-20250929",
            metadata={
                "last_failure_type": "llm_call_failure",
                "last_failure_reason": "quota exceeded",
            },
        )
        report = self._create_report(task)
        self._complete_report(report.id, verdict="FAIL")

        task.refresh_from_db()
        # Still retries (goes to IN_PROGRESS) but keeps same agent
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        self.assertEqual(task.assignee_id, self.claude_agent.id)

        # Should post a warning comment
        comment = TaskComment.objects.filter(
            task=task, author_email="system@taskit",
        ).order_by("-created_at").first()
        self.assertIn("no alternative agent", comment.content)

    # ── Non-quota failure should NOT reassign ────────────────────

    @patch("tasks.execution.get_strategy")
    def test_non_quota_failure_does_not_reassign(self, mock_get_strategy):
        """Normal NEEDS_WORK (code quality issue) should NOT reassign."""
        mock_get_strategy.return_value = MagicMock()

        task = self.make_task(
            self.board,
            status=TaskStatus.REVIEW,
            assignee=self.claude_agent,
            model_name="claude-sonnet-4-5-20250929",
        )
        report = self._create_report(task)
        self._complete_report(
            report.id,
            verdict="NEEDS_WORK",
            verdict_summary="Code quality needs improvement.",
        )

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        # Should keep the same agent
        self.assertEqual(task.assignee_id, self.claude_agent.id)
        self.assertEqual(task.model_name, "claude-sonnet-4-5-20250929")

    @patch("tasks.execution.get_strategy")
    def test_negative_quota_field_phrasing_does_not_reassign(self, mock_get_strategy):
        """F45 regression: a quota_failure field that NEGATES quota in free text
        ("None detected in current execution output.") must not be coerced into
        a quota failure. The old check treated anything != "none." as positive
        and reassigned task #114 to a dead provider."""
        mock_get_strategy.return_value = MagicMock()

        task = self.make_task(
            self.board,
            status=TaskStatus.REVIEW,
            assignee=self.claude_agent,
            model_name="claude-sonnet-4-5-20250929",
        )
        report = self._create_report(task)
        self._complete_report(
            report.id,
            verdict="NEEDS_WORK",
            verdict_summary="Residual doc comment needs fixing.",
            quota_failure="None detected in current execution output.",
        )

        task.refresh_from_db()
        # Rework loop keeps the same agent and model — no quota reassignment.
        self.assertEqual(task.assignee_id, self.claude_agent.id)
        self.assertEqual(task.model_name, "claude-sonnet-4-5-20250929")

    @patch("tasks.execution.get_strategy")
    def test_retired_agent_never_selected_for_reassignment(self, mock_get_strategy):
        """F45 regression: agents absent from agent_models.json (retired gemini,
        qwen) must never be reassignment targets, even when their DB rows and
        board memberships still exist and they sort first by id."""
        mock_get_strategy.return_value = MagicMock()

        retired = User.objects.create(
            name="gemini", email="gemini@odin.agent", role=UserRole.AGENT,
            available_models=["gemini-3-flash-preview"],
        )
        BoardMembership.objects.create(board=self.board, user=retired)

        task = self.make_task(
            self.board,
            status=TaskStatus.REVIEW,
            assignee=self.claude_agent,
            model_name="claude-sonnet-4-5-20250929",
            metadata={
                "last_failure_type": "llm_call_failure",
                "last_failure_reason": "HTTP 429: rate limit exceeded",
            },
        )
        report = self._create_report(task)
        self._complete_report(report.id, verdict="FAIL", verdict_summary="Rate limit hit.")

        task.refresh_from_db()
        # Reassigned to the ACTIVE alternative with its lineup default model.
        self.assertEqual(task.assignee_id, self.glm_agent.id)
        self.assertEqual(task.model_name, "zai-coding-plan/glm-5.2")


class TestDefaultModelOnAssign(APITestCase):
    """Default First (F45): setting an assignee without an explicit model must
    resolve the agent's default model from agent_models.json — a model-less
    task must never reach dispatch."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.glm_agent = User.objects.create(
            name="glm", email="glm@odin.agent", role=UserRole.AGENT,
        )
        # W10.4: TaskViewSet.assign rejects un-enrolled agents. This test
        # class exercises the F45 default-model path, so the agent must
        # be on the board's roster for the assign action to succeed.
        BoardMembership.objects.create(board=self.board, user=self.glm_agent)

    def test_assign_action_defaults_model(self):
        task = self.make_task(self.board, model_name=None)
        resp = self.client.post(
            f"/tasks/{task.id}/assign/",
            {"assignee_id": self.glm_agent.id, "updated_by": "op@test.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.model_name, "zai-coding-plan/glm-5.2")
        self.assertTrue(TaskHistory.objects.filter(
            task=task, field_name="model_name", new_value="zai-coding-plan/glm-5.2",
        ).exists())

    def test_assign_action_keeps_explicit_model(self):
        task = self.make_task(self.board, model_name="zai-coding-plan/glm-5.2")
        resp = self.client.post(
            f"/tasks/{task.id}/assign/",
            {"assignee_id": self.glm_agent.id, "updated_by": "op@test.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.model_name, "zai-coding-plan/glm-5.2")

    def test_patch_assignee_defaults_model(self):
        task = self.make_task(self.board, model_name=None)
        resp = self.client.patch(
            f"/tasks/{task.id}/",
            {"assignee_id": self.glm_agent.id, "updated_by": "op@test.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.model_name, "zai-coding-plan/glm-5.2")

    def test_patch_assignee_respects_explicit_model_in_same_request(self):
        task = self.make_task(self.board, model_name=None)
        resp = self.client.patch(
            f"/tasks/{task.id}/",
            {"assignee_id": self.glm_agent.id, "model_name": "zai-coding-plan/glm-5.2",
             "updated_by": "op@test.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.model_name, "zai-coding-plan/glm-5.2")
