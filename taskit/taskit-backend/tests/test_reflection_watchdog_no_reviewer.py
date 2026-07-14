"""Tests for the reflection watchdog's no-reviewer escalation guard.

Bug #246: when ``select_reviewer_by_context_size`` returns (None, None) the
watchdog currently logs "no reviewer available — skipping" and adds the task
to ``result["skipped"]`` forever. There is no guard that escalates after N
silent skips, so a stuck review with no configured provider stays invisible.

These tests pin the contract:
1. First skip is silent — operator shouldn't be paged for a transient blip.
2. After N consecutive no-reviewer skips, post a QUESTION comment naming the
   "reviewer" / "human" problem and move the task into ``result["escalated"]``.
3. The escalation is idempotent — re-running the scan does not post a second
   QUESTION comment for the same task.

The threshold N is driven via the setting
``REFLECTION_WATCHDOG_NO_REVIEWER_SKIPS_BEFORE_ESCALATION`` (default 3) so
the test does not hard-code the implementation number.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone

from tasks.models import (
    CommentType,
    TaskComment,
    TaskStatus,
)
from tests.base import APITestCase

_NO_REVIEWER_THRESHOLD = 3
_NO_REVIEWER_PATCH = patch(
    "tasks.views.select_reviewer_by_context_size",
    return_value=(None, None, "default"),
)


class _NoReviewerFixture(APITestCase):
    """Shared fixture: board, REVIEW task, patched no-reviewer selection."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board, status=TaskStatus.REVIEW)


@override_settings(REFLECTION_WATCHDOG_NO_REVIEWER_SKIPS_BEFORE_ESCALATION=_NO_REVIEWER_THRESHOLD)
class FirstSkipIsSilentTests(_NoReviewerFixture):
    """A single no-reviewer skip must not page the operator."""

    @_NO_REVIEWER_PATCH
    def test_no_reviewer_first_skip_is_silent(self, _mock_reviewer):
        from tasks.dag_executor import scan_pending_reflections

        result = scan_pending_reflections(minutes_idle=0, max_attempts=99)

        self.assertIn(self.task.id, result["skipped"])
        self.assertNotIn(self.task.id, result["escalated"])
        self.assertNotIn(self.task.id, result["dispatched"])
        question_count = TaskComment.objects.filter(
            task=self.task, comment_type=CommentType.QUESTION,
        ).count()
        self.assertEqual(question_count, 0)


@override_settings(REFLECTION_WATCHDOG_NO_REVIEWER_SKIPS_BEFORE_ESCALATION=_NO_REVIEWER_THRESHOLD)
class EscalationAfterRepeatedSkipsTests(_NoReviewerFixture):
    """After N consecutive no-reviewer skips, escalate with a QUESTION comment."""

    @_NO_REVIEWER_PATCH
    @patch("tasks.dag_executor._dispatch_reflection_task")
    def test_no_reviewer_after_n_skips_escalates_with_question_comment(
        self, _mock_dispatch, _mock_reviewer,
    ):
        from tasks.dag_executor import scan_pending_reflections

        # Drive the threshold: call exactly N times. The first N-1 calls must
        # stay in "skipped"; the Nth call must escalate.
        for i in range(1, _NO_REVIEWER_THRESHOLD):
            result = scan_pending_reflections(minutes_idle=0, max_attempts=99)
            self.assertIn(
                self.task.id, result["skipped"],
                f"call #{i} should be silent skip",
            )
            self.assertNotIn(
                self.task.id, result["escalated"],
                f"call #{i} must not escalate before threshold",
            )

        result = scan_pending_reflections(minutes_idle=0, max_attempts=99)
        self.assertIn(
            self.task.id, result["escalated"],
            "Nth no-reviewer skip must escalate",
        )
        self.assertNotIn(self.task.id, result["skipped"])

        question = TaskComment.objects.filter(
            task=self.task, comment_type=CommentType.QUESTION,
        ).first()
        self.assertIsNotNone(question, "escalation must post a QUESTION comment")
        content = question.content.lower()
        self.assertIn("reviewer", content)
        self.assertIn("human", content)

    @_NO_REVIEWER_PATCH
    @patch("tasks.dag_executor._dispatch_reflection_task")
    def test_no_reviewer_escalation_uses_idempotent_marker(
        self, _mock_dispatch, _mock_reviewer,
    ):
        from tasks.dag_executor import scan_pending_reflections

        for _ in range(_NO_REVIEWER_THRESHOLD):
            scan_pending_reflections(minutes_idle=0, max_attempts=99)

        self.task.refresh_from_db()
        self.assertIsNotNone(
            (self.task.metadata or {}).get(
                "reflection_watchdog_no_reviewer_escalated_at"
            ),
            "escalation must stamp an idempotent marker on task metadata",
        )

        before = TaskComment.objects.filter(
            task=self.task, comment_type=CommentType.QUESTION,
        ).count()

        # Re-run the scan; no second QUESTION comment should land.
        scan_pending_reflections(minutes_idle=0, max_attempts=99)
        after = TaskComment.objects.filter(
            task=self.task, comment_type=CommentType.QUESTION,
        ).count()
        self.assertEqual(
            after, before,
            "re-running the scan must not post a second QUESTION comment",
        )