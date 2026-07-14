"""F45 rework-continuity regression tests.

Origin: docs/fable_roadmap/OPERATIONS.md F45 (`rework keeps agent+model by default` mandate).

Pins three behaviors the F45 mandate demands and the existing test suite does
not cover:

  1. **Default First on rework.** When a reflection comes back NEEDS_WORK for a
     non-quota reason (code quality, missed edge case, etc.), the task moves
     REVIEW → IN_PROGRESS **with the same assignee and the same model**. The
     operator picked that lineup on purpose; the rework loop must not silently
     swap either field. This is the headline F45 mandate.

  2. **Quota path is NOT regressed.** The pre-F45 quota reassignment logic
     must still fire when the failure is genuinely a quota/rate-limit error.
     F45 changes the default; it does not break the existing escape hatch.

  3. **Three-strike cap still FAILEDs the task.** After three completed
     reflections the task moves to FAILED with the same exact comment text
     the existing code writes. F45 changes defaults; it does not weaken the
     retry cap.

Each test class quotes the F45 mandate in its docstring so the contract the
test pins is searchable from the failure trace.

Non-visual — no browser/screenshots needed.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from tests.base import APITestCase
from tasks.models import (
    BoardMembership,
    CommentType,
    ReflectionReport,
    ReflectionStatus,
    Task,
    TaskComment,
    TaskHistory,
    TaskStatus,
    User,
    UserRole,
)


F45_MANDATE_QUOTE = (
    "rework keeps agent+model by default"
)


class ReworkContinuityRegression(APITestCase):
    """F45 mandate #1: "{F45_MANDATE_QUOTE}".

    Non-quota NEEDS_WORK must move the task back to IN_PROGRESS **without**
    touching assignee or model_name. The F45 default is to keep what the
    operator originally picked; only an explicit quota signal (verified by
    the second test class) is allowed to reassign.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        # The "operator-picked" agent the task is assigned to.
        self.claude = User.objects.create(
            name="claude",
            email="claude@odin.agent",
            role=UserRole.AGENT,
            available_models=["claude-sonnet-4-5"],
        )
        # Board membership is required for any auto-reassignment logic to
        # even consider the agent as a candidate. Single-agent board on
        # purpose: there is NO alternative, so any reassign would be wrong.
        BoardMembership.objects.create(board=self.board, user=self.claude)

    def _create_running_report(self, task):
        """A report in RUNNING state that we then complete via the API."""
        return ReflectionReport.objects.create(
            task=task,
            reviewer_agent="claude",
            reviewer_model="claude-sonnet-4-5",
            requested_by="system@taskit",
            status=ReflectionStatus.RUNNING,
        )

    def _complete_with_needs_work(self, report, quota_failure="none."):
        """PATCH /reflections/{id}/ with a non-quota NEEDS_WORK verdict.

        The verdict_summary MUST NOT contain any of the _QUOTA_KEYWORDS
        ("quota", "rate limit", "429", "too many requests", "usage limit",
        "out of quota", "quota exceeded", "quota_failure") — otherwise the
        production `_is_quota_failure` check at views.py lines 378-380 takes
        the quota branch and our F45 default-first assertion is muddied.
        """
        return self.client.patch(
            f"/reflections/{report.id}/",
            {
                "status": "COMPLETED",
                "verdict": "NEEDS_WORK",
                "verdict_summary": "Code quality needs improvement; tests still red.",
                "quota_failure": quota_failure,
            },
            format="json",
        )

    @patch("tasks.execution.get_strategy")
    def test_non_quota_needs_work_keeps_assignee_and_model(self, mock_get_strategy):
        """Non-quota NEEDS_WORK → IN_PROGRESS, assignee+model UNCHANGED.

        Verifies every observable the F45 mandate requires:
          - task.status == IN_PROGRESS
          - task.assignee_id unchanged
          - task.model_name unchanged
          - a TaskHistory row records the unchanged assignee with the
            "rework_continuity" reason (today's code does not write this)
          - a TaskHistory row records the unchanged model with the
            "rework_continuity" reason (today's code does not write this)
          - a STATUS_UPDATE comment narrates that the agent was kept, so the
            audit trail makes the "continuity" decision human-visible.
        """
        mock_get_strategy.return_value = MagicMock()

        task = self.make_task(
            self.board,
            title="F45 continuity check",
            status=TaskStatus.REVIEW,
            assignee=self.claude,
            model_name="claude-sonnet-4-5",
        )
        original_assignee_id = task.assignee_id
        original_model = task.model_name

        report = self._create_running_report(task)
        resp = self._complete_with_needs_work(report, quota_failure="none.")
        self.assertEqual(resp.status_code, 200, resp.data)

        task.refresh_from_db()

        # 1) Status moved REVIEW → IN_PROGRESS.
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)

        # 2) Assignee unchanged (the headline F45 invariant).
        self.assertEqual(task.assignee_id, original_assignee_id)
        self.assertIsNotNone(
            task.assignee_id,
            "precondition broken: task must have an assignee for F45 to bite",
        )

        # 3) Model unchanged.
        self.assertEqual(task.model_name, original_model)

        # 4) An assignee history row records the continuity decision. Today
        #    the non-quota NEEDS_WORK branch (views.py lines 3386-3401) does
        #    NOT create an assignee/model history row — only a status row.
        #    F45 demands both rows be written so the audit trail shows the
        #    operator's agent+model were intentionally preserved.
        assignee_history = TaskHistory.objects.filter(
            task=task,
            field_name="assignee",
            changed_by="system@taskit",
        )
        self.assertTrue(
            assignee_history.exists(),
            "F45 mandates an assignee history row recording continuity; "
            "views.py today does not write one for non-quota NEEDS_WORK.",
        )
        latest_assignee = assignee_history.latest("changed_at")
        self.assertEqual(
            latest_assignee.new_value,
            str(original_assignee_id),
            "F45 history row must record the assignee id (preserved, not changed).",
        )
        # The reason must be reachable from the history row. The F45 contract
        # allows encoding it either as a column on TaskHistory or in
        # task.metadata["last_rework_reason"]. We accept either location so
        # the test doesn't lock the implementation to one shape.
        reason_on_row = (
            getattr(latest_assignee, "reason", None)
            or (latest_assignee.metadata or {}).get("reason")
        )
        reason_in_meta = (task.metadata or {}).get("last_rework_reason")
        self.assertIn(
            "rework_continuity",
            {reason_on_row, reason_in_meta},
            "F45 requires the rework_continuity reason to be recorded on "
            "the assignee history row (column or row metadata) or in "
            "task.metadata['last_rework_reason'].",
        )

        # 5) A model history row likewise records continuity.
        model_history = TaskHistory.objects.filter(
            task=task,
            field_name="model",
            changed_by="system@taskit",
        )
        self.assertTrue(
            model_history.exists(),
            "F45 mandates a model history row recording continuity; "
            "views.py today does not write one for non-quota NEEDS_WORK.",
        )
        latest_model = model_history.latest("changed_at")
        self.assertEqual(
            latest_model.new_value,
            original_model,
            "F45 history row must record the preserved model value.",
        )
        model_reason_on_row = (
            getattr(latest_model, "reason", None)
            or (latest_model.metadata or {}).get("reason")
        )
        self.assertIn(
            "rework_continuity",
            {model_reason_on_row, reason_in_meta},
            "F45 requires the rework_continuity reason for the model row too.",
        )

        # 6) A human-visible STATUS_UPDATE comment narrates the decision.
        continuity_comments = TaskComment.objects.filter(
            task=task,
            comment_type=CommentType.STATUS_UPDATE,
            author_email="system@taskit",
        )
        contents = " || ".join(c.content for c in continuity_comments).lower()
        self.assertIn(
            "kept", contents,
            "F45 requires a STATUS_UPDATE comment recording that the agent "
            "was kept; views.py today does not write one for non-quota NEEDS_WORK.",
        )
        self.assertIn(
            "continuity", contents,
            "F45 requires a STATUS_UPDATE comment mentioning 'continuity'.",
        )

    @patch("tasks.execution.get_strategy")
    def test_non_quota_needs_work_records_reason_in_metadata(self, mock_get_strategy):
        """task.metadata carries last_rework_reason + last_rework_at.

        F45 mandates these stamps on the task itself so any later observer
        (status reports, dashboards, follow-up automations) can tell at a
        glance whether a transition was a continuity-preserving rework or
        a quota-driven reassignment. Today's code does not set either key.
        """
        mock_get_strategy.return_value = MagicMock()

        task = self.make_task(
            self.board,
            title="Metadata continuity stamp",
            status=TaskStatus.REVIEW,
            assignee=self.claude,
            model_name="claude-sonnet-4-5",
        )
        report = self._create_running_report(task)

        before = datetime.now(timezone.utc)
        resp = self._complete_with_needs_work(report, quota_failure="none.")
        self.assertEqual(resp.status_code, 200, resp.data)
        after = datetime.now(timezone.utc) + timedelta(seconds=1)

        task.refresh_from_db()
        meta = task.metadata or {}

        self.assertEqual(
            meta.get("last_rework_reason"),
            "rework_continuity",
            "F45 mandates task.metadata['last_rework_reason'] == "
            "'rework_continuity' on a non-quota NEEDS_WORK; today the key "
            "is never written.",
        )

        # The timestamp must parse as an ISO string and fall within the
        # window bracketed by the call. Allow a small fudge factor on the
        # leading edge because some serializers drop sub-second precision.
        raw_at = meta.get("last_rework_at")
        self.assertIsNotNone(
            raw_at,
            "F45 mandates task.metadata['last_rework_at']; today it is unset.",
        )
        stamped = datetime.fromisoformat(str(raw_at).replace("Z", "+00:00"))
        if stamped.tzinfo is None:
            stamped = stamped.replace(tzinfo=timezone.utc)
        self.assertGreaterEqual(
            stamped, before - timedelta(seconds=1),
            "last_rework_at must be a recent timestamp, not stale data.",
        )
        self.assertLessEqual(
            stamped, after,
            "last_rework_at must be at or before the request returned.",
        )


class QuotaReassignmentPreserved(APITestCase):
    """F45 mandate: quota path still works.

    F45 changes the *default* (continuity). It must NOT break the existing
    quota/rate-limit reassignment behavior — that path is what saves the
    task when the operator's chosen agent is genuinely exhausted. If F45's
    continuity logic accidentally suppresses the quota path, this test
    catches the regression.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.claude = User.objects.create(
            name="claude",
            email="claude@odin.agent",
            role=UserRole.AGENT,
            available_models=["claude-sonnet-4-5"],
        )
        self.glm = User.objects.create(
            name="glm",
            email="glm@odin.agent",
            role=UserRole.AGENT,
            available_models=["zai-coding-plan/glm-5.2"],
        )
        BoardMembership.objects.create(board=self.board, user=self.claude)
        BoardMembership.objects.create(board=self.board, user=self.glm)
        # Task #159: quota reassignment now verifies against ground truth.
        # Mock the provider as genuinely exhausted so this test continues to
        # pin the *verified* reassignment path.
        usage_patch = patch("tasks.views._get_usage_from_provider")
        self.mock_usage = usage_patch.start()
        self.addCleanup(usage_patch.stop)
        self.mock_usage.return_value = (98.0, None)

    @patch("tasks.execution.get_strategy")
    def test_quota_failure_still_reassigns_unchanged(self, mock_get_strategy):
        """Quota-keyword verdict_summary → reassign to a different agent+model.

        Mirrors the pre-F45 behavior the operator relied on. If this test
        ever fails, F45 has gone too far and broken the quota escape hatch.
        """
        mock_get_strategy.return_value = MagicMock()

        task = self.make_task(
            self.board,
            title="Quota exhaustion case",
            status=TaskStatus.REVIEW,
            assignee=self.claude,
            model_name="claude-sonnet-4-5",
        )
        report = ReflectionReport.objects.create(
            task=task,
            reviewer_agent="claude",
            reviewer_model="claude-sonnet-4-5",
            requested_by="system@taskit",
            status=ReflectionStatus.RUNNING,
        )

        # "quota exceeded" is one of the _QUOTA_KEYWORDS the production code
        # scans for in verdict_summary (views.py line 346).
        resp = self.client.patch(
            f"/reflections/{report.id}/",
            {
                "status": "COMPLETED",
                "verdict": "NEEDS_WORK",
                "verdict_summary": "Agent failed: quota exceeded on Claude.",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)

        task.refresh_from_db()

        # Quota path still fires — assignee is the alternative agent.
        self.assertNotEqual(
            task.assignee_id, self.claude.id,
            "Quota path must reassign away from the exhausted claude agent.",
        )
        self.assertEqual(
            task.assignee_id, self.glm.id,
            "Quota reassignment should land on the only other board member.",
        )
        # Model_name was overwritten by the reassignment (glm's default).
        self.assertNotEqual(
            task.model_name, "claude-sonnet-4-5",
            "Quota path must update model_name to the new agent's model.",
        )
        # The standard quota-reassignment comment is still posted.
        comment = TaskComment.objects.filter(
            task=task, author_email="system@taskit",
        ).order_by("-created_at").first()
        self.assertIsNotNone(comment)
        self.assertIn("Quota", comment.content)


class ThreeStrikeFailStillFires(APITestCase):
    """F45 mandate: original 3-strike policy preserved.

    F45 changes the *default* for the first two NEEDS_WORK verdicts. After
    three completed reflections the task still moves to FAILED with the
    exact comment the existing code writes. If F45 accidentally lets a
    task loop forever on a misbehaving reviewer, this test catches it.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.user = self.make_user(name="claude", email="claude@test.com")
        # Three-strike cap is independent of the agent, but having a real
        # assignee makes the test more realistic and matches the production
        # data shape.
        self.task = self.make_task(
            self.board,
            title="Three-strike candidate",
            status=TaskStatus.REVIEW,
            assignee=self.user,
            model_name="claude-sonnet-4-5",
        )

    def _completed_needs_work(self, verdict_summary="Needs improvement."):
        """Plant a directly-COMPLETED report that counts toward the cap."""
        return ReflectionReport.objects.create(
            task=self.task,
            reviewer_agent="claude",
            reviewer_model="claude-sonnet-4-5",
            requested_by="system@taskit",
            status=ReflectionStatus.COMPLETED,
            verdict="NEEDS_WORK",
            verdict_summary=verdict_summary,
        )

    def test_three_needs_work_reflections_fail_the_task(self):
        """3 completed NEEDS_WORK reports → task FAILED, exact comment preserved.

        Two reports are planted directly as COMPLETED. The third is
        completed through the API; the cap (count >= 3) is what triggers
        the FAILED transition with the exact comment text the existing
        code emits at views.py line 3373.
        """
        self._completed_needs_work("Attempt 1: failed to compile.")
        self._completed_needs_work("Attempt 2: tests still red.")

        third = ReflectionReport.objects.create(
            task=self.task,
            reviewer_agent="claude",
            reviewer_model="claude-sonnet-4-5",
            requested_by="system@taskit",
            status=ReflectionStatus.RUNNING,
        )

        resp = self.client.patch(
            f"/reflections/{third.id}/",
            {
                "status": "COMPLETED",
                "verdict": "NEEDS_WORK",
                "verdict_summary": "Attempt 3: still failing.",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)

        self.task.refresh_from_db()
        self.assertEqual(
            self.task.status, TaskStatus.FAILED,
            "F45 must not weaken the three-strike retry cap; 3 NEEDS_WORK "
            "reflections still FAILED the task.",
        )

        # The exact status comment the existing code writes — verbatim, so
        # any change here is a deliberate contract change and not a typo.
        failure_comment = TaskComment.objects.filter(
            task=self.task,
            author_email="system@taskit",
            comment_type=CommentType.STATUS_UPDATE,
        ).order_by("-created_at").first()
        self.assertIsNotNone(
            failure_comment,
            "Three-strike FAIL must post a STATUS_UPDATE comment.",
        )
        self.assertEqual(
            failure_comment.content,
            "Task failed after 3 reflection attempts without passing.",
            "F45 must preserve the exact failure-comment text the existing "
            "code emits at views.py line 3373 — operators rely on it.",
        )

        # And the status history row was recorded for the audit trail.
        status_history = TaskHistory.objects.filter(
            task=self.task,
            field_name="status",
            changed_by="system@taskit",
        ).order_by("-changed_at").first()
        self.assertIsNotNone(status_history)
        self.assertEqual(status_history.old_value, TaskStatus.REVIEW)
        self.assertEqual(status_history.new_value, TaskStatus.FAILED)