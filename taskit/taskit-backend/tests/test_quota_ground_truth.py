"""Quota-failover ground-truth verification tests (task #159).

Origin: a healthy provider was reassigned on a "quota/rate-limit failure" while
its real usage showed ample headroom (MiniMax at 64/100 in the 5h window). The
production `_is_quota_failure` keyword-matches over reflection free-text and
failure reasons, so a transient per-minute 429 reads identically to real quota
exhaustion. The fix consults the ground-truth checker
(`harness_usage_status`) before reassigning, and branches:

  - EXHAUSTED (>= _QUOTA_EXHAUSTED_PCT)        → reassign (the real escape hatch)
  - HEADROOM (< threshold)                     → keep agent, record backoff, requeue
  - UNAVAILABLE (checker missing/errored)      → fall back to keyword reassign,
                                                 flagged "unverified" in the comment

These tests pin all three branches plus the gate (non-quota failures must not
even consult the checker). The network boundary is `_get_usage_from_provider`,
which is mocked everywhere — the real `harness_usage_status` package is not a
test dependency.

Non-visual — no browser/screenshots needed.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import MagicMock, patch

from tests.base import APITestCase
from tasks.models import (
    BoardMembership,
    CommentType,
    ReflectionReport,
    ReflectionStatus,
    Task,
    TaskComment,
    TaskStatus,
    User,
    UserRole,
)
from tasks.views import (
    _check_provider_usage,
    _QUOTA_EXHAUSTED,
    _QUOTA_HEADROOM,
    _QUOTA_UNAVAILABLE,
)


class QuotaGroundTruthFailover(APITestCase):
    """The three failover branches + the non-quota gate (task #159)."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        # minimax is the task-154 assignee; glm is the healthy alternative.
        self.minimax = User.objects.create(
            name="minimax", email="minimax@odin.agent", role=UserRole.AGENT,
            available_models=["MiniMax-M1"],
        )
        self.glm = User.objects.create(
            name="glm", email="glm@odin.agent", role=UserRole.AGENT,
            available_models=["zai-coding-plan/glm-5.2"],
        )
        self.claude = User.objects.create(
            name="claude", email="claude@odin.agent", role=UserRole.AGENT,
            available_models=["claude-sonnet-4-5"],
        )
        for u in (self.minimax, self.glm, self.claude):
            BoardMembership.objects.create(board=self.board, user=u)

        # Default: pretend the checker is unavailable so tests that don't care
        # about usage fall through to the keyword fallback deterministically.
        # Per-test overrides change this.
        self._usage_patch = patch("tasks.views._get_usage_from_provider")
        self.mock_usage = self._usage_patch.start()
        self.addCleanup(self._usage_patch.stop)
        self.mock_usage.return_value = (None, "harness_usage_status package not installed")

    def _task_on(self, assignee, model, **meta):
        return self.make_task(
            self.board,
            status=TaskStatus.REVIEW,
            assignee=assignee,
            model_name=model,
            metadata=meta or {},
        )

    def _running_report(self, task):
        reviewer = task.assignee.name if task.assignee else "minimax"
        return ReflectionReport.objects.create(
            task=task,
            reviewer_agent=reviewer,
            reviewer_model=task.model_name or "MiniMax-M1",
            requested_by="system@taskit",
            status=ReflectionStatus.RUNNING,
        )

    def _complete(self, report, verdict="NEEDS_WORK", **extra):
        payload = {
            "status": "COMPLETED",
            "verdict": verdict,
            "verdict_summary": "Reflection complete.",
            **extra,
        }
        return self.client.patch(f"/reflections/{report.id}/", payload, format="json")

    def _latest_comment(self, task):
        return TaskComment.objects.filter(
            task=task, author_email="system@taskit",
        ).order_by("-created_at").first()

    # ── 1. EXHAUSTED → reassign, comment names the verified path ───────────

    @patch("tasks.execution.get_strategy")
    def test_exhausted_provider_reassigns_with_verified_comment(self, mock_strategy):
        mock_strategy.return_value = MagicMock()
        self.mock_usage.return_value = (98.0, None)  # minimax genuinely exhausted

        task = self._task_on(
            self.minimax, "MiniMax-M1",
            last_failure_type="llm_call_failure",
            last_failure_reason="HTTP 429: quota exceeded",
        )
        report = self._running_report(task)
        self._complete(report, verdict="FAIL", verdict_summary="quota exceeded on minimax.")

        task.refresh_from_db()
        self.assertEqual(task.assignee_id, self.glm.id, "Exhausted provider must reassign.")
        self.assertNotEqual(task.model_name, "MiniMax-M1")

        comment = self._latest_comment(task)
        self.assertIsNotNone(comment)
        self.assertIn("VERIFIED", comment.content, "Comment must name the verified path.")
        self.assertIn("98.0%", comment.content, "Comment must cite the real usage figure.")
        self.assertIn("Reassigned", comment.content)

    # ── 2. HEADROOM (task-154 replay) → keep agent, record backoff ─────────

    @patch("tasks.execution.get_strategy")
    def test_task154_replay_429_with_headroom_keeps_agent(self, mock_strategy):
        """The exact incident: a transient 429 while the provider is at 64%.

        Reassigning here was the bug. With ground truth, the provider has
        headroom, so we keep minimax, record metadata.rate_limit_backoff, and
        let the caller requeue the same agent.
        """
        mock_strategy.return_value = MagicMock()
        self.mock_usage.return_value = (64.0, None)  # the operator's real screenshot

        task = self._task_on(
            self.minimax, "MiniMax-M1",
            last_failure_type="llm_call_failure",
            last_failure_reason="HTTP 429: rate limit exceeded",
        )
        report = self._running_report(task)
        self._complete(report, verdict="FAIL", verdict_summary="rate limit hit (429).")

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS, "Task still requeues.")
        self.assertEqual(
            task.assignee_id, self.minimax.id,
            "Provider with headroom must NOT be reassigned away.",
        )
        self.assertEqual(task.model_name, "MiniMax-M1", "Model is preserved.")

        # Backoff signal recorded so repeats are visible.
        self.assertIn("rate_limit_backoff", task.metadata or {})
        backoff = task.metadata["rate_limit_backoff"]
        self.assertEqual(backoff["agent"], "minimax")
        self.assertEqual(backoff["usage_pct"], 64.0)
        self.assertIn("at", backoff)

        comment = self._latest_comment(task)
        self.assertIsNotNone(comment)
        self.assertIn("headroom", comment.content)
        self.assertIn("backoff", comment.content)
        self.assertNotIn("Reassigned", comment.content, "Must not claim a reassignment.")

    # ── 3. UNAVAILABLE → fall back to reassign, flagged unverified ─────────

    @patch("tasks.execution.get_strategy")
    def test_checker_unavailable_falls_back_to_reassign(self, mock_strategy):
        """When the checker itself is down, preserve the pre-#159 behavior
        (reassign on keyword match) but say so honestly in the comment."""
        mock_strategy.return_value = MagicMock()
        self.mock_usage.return_value = (None, "harness_usage_status package not installed")

        task = self._task_on(
            self.minimax, "MiniMax-M1",
            last_failure_type="llm_call_failure",
            last_failure_reason="HTTP 429: rate limit exceeded",
        )
        report = self._running_report(task)
        self._complete(report, verdict="FAIL", verdict_summary="rate limit hit.")

        task.refresh_from_db()
        self.assertEqual(
            task.assignee_id, self.glm.id,
            "Checker-down path keeps the old behavior: reassign.",
        )

        comment = self._latest_comment(task)
        self.assertIsNotNone(comment)
        self.assertIn("unverified", comment.content)
        self.assertIn("Reassigned", comment.content)

    # ── 4. UNAVAILABLE + no alternative → names both ───────────────────────

    @patch("tasks.execution.get_strategy")
    def test_checker_unavailable_and_no_alternative_names_both(self, mock_strategy):
        mock_strategy.return_value = MagicMock()
        self.mock_usage.return_value = (None, "usage check raised: timeout")
        # No alternative on the board.
        BoardMembership.objects.filter(user=self.glm).delete()
        self.glm.delete()
        BoardMembership.objects.filter(user=self.claude).delete()
        self.claude.delete()

        task = self._task_on(
            self.minimax, "MiniMax-M1",
            last_failure_type="llm_call_failure",
            last_failure_reason="HTTP 429: rate limit exceeded",
        )
        report = self._running_report(task)
        self._complete(report, verdict="FAIL", verdict_summary="rate limit hit.")

        task.refresh_from_db()
        self.assertEqual(task.assignee_id, self.minimax.id, "Nothing to reassign to.")

        comment = self._latest_comment(task)
        self.assertIsNotNone(comment)
        self.assertIn("unverified", comment.content)
        self.assertIn("no alternative agent", comment.content)

    # ── 5. Non-quota failure must NOT consult the checker ───────────────────

    @patch("tasks.execution.get_strategy")
    def test_non_quota_failure_does_not_consult_checker(self, mock_strategy):
        """A normal code-quality NEEDS_WORK is not quota-related, so the
        ground-truth checker must never be called (cheap path stays cheap)."""
        mock_strategy.return_value = MagicMock()

        task = self._task_on(self.minimax, "MiniMax-M1")
        report = self._running_report(task)
        self._complete(report, verdict="NEEDS_WORK", verdict_summary="Code quality needs work.")

        task.refresh_from_db()
        self.assertEqual(task.assignee_id, self.minimax.id)
        self.mock_usage.assert_not_called()


class QuotaThresholdBoundary(APITestCase):
    """Direct unit tests of the threshold policy in `_check_provider_usage`."""

    def test_boundary_at_threshold_is_exhausted(self):
        with patch("tasks.views._get_usage_from_provider", return_value=(95.0, None)):
            result = _check_provider_usage("minimax")
        self.assertEqual(result.state, _QUOTA_EXHAUSTED)
        self.assertEqual(result.usage_pct, 95.0)

    def test_just_below_threshold_is_headroom(self):
        with patch("tasks.views._get_usage_from_provider", return_value=(94.9, None)):
            result = _check_provider_usage("minimax")
        self.assertEqual(result.state, _QUOTA_HEADROOM)
        self.assertEqual(result.usage_pct, 94.9)

    def test_checker_error_is_unavailable(self):
        with patch("tasks.views._get_usage_from_provider",
                   return_value=(None, "harness_usage_status package not installed")):
            result = _check_provider_usage("minimax")
        self.assertEqual(result.state, _QUOTA_UNAVAILABLE)
        self.assertIsNone(result.usage_pct)
        self.assertIn("not installed", result.detail)

    def test_unmapped_agent_is_unavailable(self):
        # An agent with no provider mapping cannot be verified.
        result = _check_provider_usage("unknown-agent")
        self.assertEqual(result.state, _QUOTA_UNAVAILABLE)

    def test_claude_maps_to_claude_code_provider(self):
        """claude is the one agent whose usage-provider name differs."""
        with patch("tasks.views._get_usage_from_provider", return_value=(12.0, None)) as m:
            result = _check_provider_usage("claude")
        self.assertEqual(result.state, _QUOTA_HEADROOM)
        m.assert_called_once_with("claude_code")
