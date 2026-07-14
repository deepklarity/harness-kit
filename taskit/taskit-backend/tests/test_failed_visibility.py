"""Fable task #335 — "a board should be incapable of sitting stuck silently."

When a task goes FAILED, the system must surface it loudly across four
seams:

  1. The board inbox (``GET /boards/<id>/inbox/``) shows FAILED tasks
     first, with the true failure reason and how many downstream tasks
     are blocked by them.
  2. ``tasks.dependencies.get_blocked_by(task)`` returns same-board
     tasks whose ``depends_on`` list contains ``task.id`` — the source
     of truth for the blocked count shown in the inbox.
  3. A ``task_failed`` push notification fires when a task ENTERS
     FAILED (via the signal that already tracks status changes), and
     ``metadata.failure_notified_at`` is stamped so the reminder beat
     knows when to escalate.
  4. A periodic beat job ``scan_for_failed_reminders`` fires a
     ``task_failed_reminder`` notification for any task that has been
     FAILED for 30+ minutes AND hasn't been reminded yet.

These tests pin the behaviour. They are expected to fail at import time
or at assertion time until the corresponding implementation lands.
"""
import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from datetime import timedelta

from django.utils import timezone

from tasks.models import (
    BoardMembership,
    Notification,
    TaskHistory,
    TaskStatus,
    User,
    UserRole,
)
from tests.base import APITestCase


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_failed_history(task, when=None, changed_by="agent@odin.agent"):
    """Stamp a TaskHistory row for status→FAILED at an exact instant."""
    history = TaskHistory.objects.create(
        task=task,
        field_name="status",
        old_value=TaskStatus.IN_PROGRESS,
        new_value=TaskStatus.FAILED,
        changed_by=changed_by,
    )
    if when is not None:
        # ``changed_at`` is auto_now_add — set it via update so the
        # failure timestamp is deterministic for the failed_at lookup.
        TaskHistory.objects.filter(pk=history.pk).update(changed_at=when)
        history.refresh_from_db()
    return history


def _set_metadata(task, key, value):
    """Mutable metadata helper that round-trips through Django's JSONField."""
    meta = dict(task.metadata or {})
    meta[key] = value
    task.metadata = meta
    task.save(update_fields=["metadata"])
    task.refresh_from_db()


# ── 1. get_blocked_by helper ────────────────────────────────────────────────


class GetBlockedByTests(APITestCase):
    """``tasks.dependencies.get_blocked_by(task)`` — same-board tasks whose
    ``depends_on`` JSON list contains ``task.id``."""

    def setUp(self):
        super().setUp()
        from tasks.dependencies import get_blocked_by

        self.get_blocked_by = get_blocked_by

    def test_no_blocked_by_returns_empty(self):
        """A task with no same-board dependents returns an empty list."""
        board = self.make_board()
        lone = self.make_task(board, title="Lone failure", status=TaskStatus.FAILED)
        self.assertEqual(self.get_blocked_by(lone), [])

    def test_one_blocker_returned(self):
        """A failed task with one dependent is returned."""
        board = self.make_board()
        failed = self.make_task(board, title="Failed", status=TaskStatus.FAILED)
        downstream = self.make_task(
            board, title="Downstream", depends_on=[str(failed.id)]
        )
        blockers = self.get_blocked_by(failed)
        self.assertEqual(len(blockers), 1)
        self.assertEqual(blockers[0].id, downstream.id)

    def test_multiple_blockers_counted(self):
        """A failed task with two dependents counts both in the inbox entry."""
        board = self.make_board()
        failed = self.make_task(board, title="Failed", status=TaskStatus.FAILED)
        self.make_task(board, title="First", depends_on=[str(failed.id)])
        self.make_task(board, title="Second", depends_on=[str(failed.id)])
        blockers = self.get_blocked_by(failed)
        self.assertEqual(len(blockers), 2)

    def test_blocker_on_different_board_excluded(self):
        """A task on another board that depends_on this one is excluded."""
        board1 = self.make_board(name="Board 1")
        board2 = self.make_board(name="Board 2")
        failed = self.make_task(board1, title="Failed", status=TaskStatus.FAILED)
        self.make_task(
            board2, title="Other board downstream", depends_on=[str(failed.id)]
        )
        self.assertEqual(self.get_blocked_by(failed), [])

    def test_self_excluded(self):
        """A task is not blocked by itself, even with circular deps."""
        board = self.make_board()
        self_loop = self.make_task(
            board, title="Loopy", status=TaskStatus.FAILED, depends_on=[]
        )
        # Manually set the task's own id in depends_on to verify exclusion.
        self_loop.depends_on = [str(self_loop.id)]
        self_loop.save(update_fields=["depends_on"])
        self_loop.refresh_from_db()
        self.assertEqual(self.get_blocked_by(self_loop), [])


# ── 2. Inbox failed_tasks list ──────────────────────────────────────────────


class InboxFailedTasksTests(APITestCase):
    """``GET /boards/<id>/inbox/`` must include a ``failed_tasks`` list
    with the true failure reason, failed-at timestamp, and blocked count."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(name="W11 board")

    def _get_inbox(self):
        return self.client.get(f"/boards/{self.board.id}/inbox/")

    def _make_failed(self, **kwargs):
        defaults = {
            "title": "Boom",
            "status": TaskStatus.FAILED,
            "metadata": {
                "last_failure_reason": "odin exec exited with code 1: npm test",
                "failure_class": "agent_execution_failure",
            },
        }
        defaults.update(kwargs)
        return self.make_task(self.board, **defaults)

    def test_failed_task_appears_with_reason_and_age(self):
        """A FAILED task with metadata.last_failure_reason surfaces it,
        plus a recent failed_at timestamp."""
        failed = self._make_failed()
        resp = self._get_inbox()
        self.assertEqual(resp.status_code, 200)
        failed_list = resp.data["failed_tasks"]
        self.assertEqual(len(failed_list), 1)
        entry = failed_list[0]
        self.assertEqual(entry["task_id"], str(failed.id))
        self.assertEqual(entry["task_title"], "Boom")
        self.assertEqual(
            entry["last_failure_reason"],
            "odin exec exited with code 1: npm test",
        )
        self.assertEqual(entry["failure_class"], "agent_execution_failure")
        self.assertIn("failed_at", entry)
        # The failed_at value must parse as ISO and be close to now.
        from datetime import datetime
        parsed = datetime.fromisoformat(
            entry["failed_at"].replace("Z", "+00:00")
        )
        delta = abs((timezone.now() - parsed).total_seconds())
        self.assertLess(delta, 120, "failed_at should be ~now for a new failure")
        self.assertEqual(resp.data["counts"]["failed_tasks"], 1)

    def test_blocked_count_calculated(self):
        """blocked_count reflects the number of same-board dependents."""
        failed = self._make_failed()
        # One dependent → blocked_count == 1
        self.make_task(self.board, title="Dep A", depends_on=[str(failed.id)])
        resp = self._get_inbox()
        self.assertEqual(resp.data["failed_tasks"][0]["blocked_count"], 1)

        # Add another dependent → blocked_count == 2
        self.make_task(self.board, title="Dep B", depends_on=[str(failed.id)])
        resp = self._get_inbox()
        self.assertEqual(resp.data["failed_tasks"][0]["blocked_count"], 2)

    def test_failed_task_on_other_board_excluded(self):
        """A FAILED task on a different board does not appear."""
        other = self.make_board(name="Other")
        self.make_task(
            other,
            title="Other failure",
            status=TaskStatus.FAILED,
            metadata={"last_failure_reason": "should not appear"},
        )
        resp = self.client.get(f"/boards/{other.id}/inbox/")
        self.assertEqual(
            [e["task_title"] for e in resp.data["failed_tasks"]],
            ["Other failure"],
        )
        # This board has zero failed tasks.
        self.assertEqual(self._get_inbox().data["counts"]["failed_tasks"], 0)

    def test_failed_at_uses_latest_taskhistory_when_available(self):
        """failed_at is the timestamp of the most recent status→FAILED
        TaskHistory row, NOT task.last_updated_at."""
        failed = self._make_failed()
        # Stamp a TaskHistory row with an unambiguous, early timestamp.
        stamp = timezone.now() - timedelta(hours=2)
        _make_failed_history(failed, when=stamp)
        resp = self._get_inbox()
        failed_at = resp.data["failed_tasks"][0]["failed_at"]
        from datetime import datetime
        parsed = datetime.fromisoformat(failed_at.replace("Z", "+00:00"))
        # Allow a one-second window for SQLite microsecond truncation.
        self.assertLess(
            abs((stamp - parsed).total_seconds()),
            2,
            f"failed_at should match the TaskHistory stamp; got {parsed!r}",
        )

    def test_empty_board_has_zero_failed_tasks(self):
        """A board with no FAILED tasks returns failed_tasks=[] and
        counts.failed_tasks=0."""
        self.make_task(
            self.board, title="In progress", status=TaskStatus.IN_PROGRESS
        )
        resp = self._get_inbox()
        self.assertEqual(resp.data["failed_tasks"], [])
        self.assertEqual(resp.data["counts"]["failed_tasks"], 0)
        # And ``counts`` has the key even when zero.
        self.assertIn("failed_tasks", resp.data["counts"])

    def test_done_task_not_in_failed_list(self):
        """Tasks in DONE / TODO / IN_PROGRESS are excluded from
        failed_tasks even if they happen to have a stale metadata field."""
        self.make_task(
            self.board,
            title="Done",
            status=TaskStatus.DONE,
            metadata={"last_failure_reason": "ignored"},
        )
        self.make_task(
            self.board,
            title="Todo",
            status=TaskStatus.TODO,
            metadata={"last_failure_reason": "ignored"},
        )
        self.make_task(
            self.board,
            title="WIP",
            status=TaskStatus.IN_PROGRESS,
            metadata={"last_failure_reason": "ignored"},
        )
        resp = self._get_inbox()
        self.assertEqual(resp.data["failed_tasks"], [])
        self.assertEqual(resp.data["counts"]["failed_tasks"], 0)


# ── 3. Notification on FAILED transition ────────────────────────────────────


class NotificationOnFailureTests(APITestCase):
    """PATCH a task to FAILED → ``task_failed`` notification fires and
    ``metadata.failure_notified_at`` is stamped."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.member = User.objects.create(
            name="Alice", email="alice@odin.agent", role=UserRole.ADMIN
        )
        BoardMembership.objects.create(board=self.board, user=self.member)
        self.task = self.make_task(
            self.board,
            title="About to fail",
            status=TaskStatus.IN_PROGRESS,
        )

    def test_failed_transition_creates_task_failed_notification(self):
        """A task transitioning to FAILED creates a Notification of type
        ``task_failed`` for at least one board member, referencing the task."""
        resp = self.client.patch(
            f"/tasks/{self.task.id}/",
            data={"status": TaskStatus.FAILED, "updated_by": "agent@odin.agent"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        notifications = Notification.objects.filter(
            task=self.task, notification_type="task_failed"
        )
        self.assertTrue(notifications.exists())
        # At least one notification is delivered to a board member.
        recipient_ids = set(
            notifications.values_list("recipient_id", flat=True)
        )
        self.assertIn(self.member.id, recipient_ids)

    def test_failed_notification_sets_metadata_failure_notified_at(self):
        """After the FAILED transition, task.metadata.failure_notified_at
        is set to a recent ISO timestamp."""
        self.client.patch(
            f"/tasks/{self.task.id}/",
            data={"status": TaskStatus.FAILED, "updated_by": "agent@odin.agent"},
            format="json",
        )
        self.task.refresh_from_db()
        self.assertIn("failure_notified_at", self.task.metadata)
        from datetime import datetime
        stamp = datetime.fromisoformat(
            self.task.metadata["failure_notified_at"].replace("Z", "+00:00")
        )
        self.assertLess(
            abs((timezone.now() - stamp).total_seconds()), 120
        )

    def test_non_failed_status_does_not_create_task_failed_notification(self):
        """PATCHing to IN_PROGRESS does not create a task_failed notification."""
        self.client.patch(
            f"/tasks/{self.task.id}/",
            data={"status": TaskStatus.IN_PROGRESS,
                  "updated_by": "agent@odin.agent"},
            format="json",
        )
        self.assertFalse(
            Notification.objects.filter(
                task=self.task, notification_type="task_failed"
            ).exists()
        )

    def test_failed_notification_body_includes_reason(self):
        """When metadata.last_failure_reason is set, the notification body
        contains it."""
        meta = dict(self.task.metadata or {})
        meta["last_failure_reason"] = "GLM exhausted retries"
        self.task.metadata = meta
        self.task.save(update_fields=["metadata"])

        self.client.patch(
            f"/tasks/{self.task.id}/",
            data={"status": TaskStatus.FAILED, "updated_by": "agent@odin.agent"},
            format="json",
        )
        notifications = Notification.objects.filter(
            task=self.task, notification_type="task_failed"
        )
        self.assertTrue(notifications.exists())
        bodies = " ".join(n.body for n in notifications)
        self.assertIn("GLM exhausted retries", bodies)


# ── 4. Failed reminder beat job ─────────────────────────────────────────────


class FailedReminderTests(APITestCase):
    """``tasks.failed_reminder.scan_for_failed_reminders`` fires a
    ``task_failed_reminder`` notification for tasks FAILED for 30+ min
    that have not been reminded yet, and only fires once."""

    def setUp(self):
        super().setUp()
        from tasks.failed_reminder import REMINDER_THRESHOLD, scan_for_failed_reminders

        self.scan_for_failed_reminders = scan_for_failed_reminders
        self.reminder_threshold = REMINDER_THRESHOLD
        self.board = self.make_board()
        self.member = User.objects.create(
            name="Alice", email="alice@odin.agent", role=UserRole.ADMIN
        )
        BoardMembership.objects.create(board=self.board, user=self.member)
        self.task = self.make_task(
            self.board,
            title="Still broken",
            status=TaskStatus.FAILED,
            metadata={
                "last_failure_reason": "stuck",
                "failure_notified_at": (
                    timezone.now() - timedelta(minutes=31)
                ).isoformat(),
            },
        )

    def test_reminder_fires_after_30_minutes(self):
        """Task failed 31 minutes ago, no reminder sent yet → beat job
        sends a task_failed_reminder notification and stamps
        metadata.failure_reminder_sent_at."""
        count = self.scan_for_failed_reminders()
        self.assertEqual(count, 1)
        reminders = Notification.objects.filter(
            task=self.task, notification_type="task_failed_reminder"
        )
        self.assertTrue(reminders.exists())

        self.task.refresh_from_db()
        self.assertIn("failure_reminder_sent_at", self.task.metadata)

    def test_reminder_not_fired_before_30_min(self):
        """Task failed only 5 minutes ago → beat job does not send a
        reminder and does not stamp metadata.failure_reminder_sent_at."""
        # Replace notified_at with a fresher stamp.
        _set_metadata(
            self.task,
            "failure_notified_at",
            (timezone.now() - timedelta(minutes=5)).isoformat(),
        )
        count = self.scan_for_failed_reminders()
        self.assertEqual(count, 0)
        self.assertFalse(
            Notification.objects.filter(
                task=self.task, notification_type="task_failed_reminder"
            ).exists()
        )
        self.task.refresh_from_db()
        self.assertNotIn("failure_reminder_sent_at", self.task.metadata)

    def test_reminder_only_fires_once(self):
        """Once metadata.failure_reminder_sent_at is stamped, the next
        beat run does NOT send another reminder (idempotent within a run,
        doesn't re-storm)."""
        # First run stamps the metadata + fires one notification.
        self.scan_for_failed_reminders()
        self.task.refresh_from_db()
        first_remainder_count = Notification.objects.filter(
            task=self.task, notification_type="task_failed_reminder"
        ).count()
        # Second run finds the task still FAILED, but the stamp is set —
        # it should be a no-op.
        count2 = self.scan_for_failed_reminders()
        self.assertEqual(count2, 0)
        second_remainder_count = Notification.objects.filter(
            task=self.task, notification_type="task_failed_reminder"
        ).count()
        self.assertEqual(second_remainder_count, first_remainder_count)

    def test_reminder_not_fired_for_non_failed_tasks(self):
        """A TODO task with an old failure_notified_at is NOT reminded —
        the reminder only targets FAILED tasks."""
        _set_metadata(
            self.task,
            "failure_notified_at",
            (timezone.now() - timedelta(hours=2)).isoformat(),
        )
        _set_metadata(self.task, "last_failure_reason", "old")
        # Move the task out of FAILED.
        self.task.status = TaskStatus.TODO
        self.task.save(update_fields=["status"])
        count = self.scan_for_failed_reminders()
        self.assertEqual(count, 0)
        self.assertFalse(
            Notification.objects.filter(
                task=self.task, notification_type="task_failed_reminder"
            ).exists()
        )

    def test_reminder_to_board_members(self):
        """The reminder notification is delivered to board members."""
        self.scan_for_failed_reminders()
        reminders = Notification.objects.filter(
            task=self.task, notification_type="task_failed_reminder"
        )
        recipient_ids = set(reminders.values_list("recipient_id", flat=True))
        self.assertIn(self.member.id, recipient_ids)

    # ── frozen-clock / injected-now boundary ──────────────────────────────
    # The tests above rely on real wall-clock time inside the beat job, so
    # they can pin "31 min ago fires, 5 min ago doesn't" but never the exact
    # threshold. These two inject ``now=`` so the boundary is deterministic
    # and the stamp is provably derived from the injected moment, not the
    # system clock.

    def test_reminder_fires_at_exact_threshold_with_frozen_clock(self):
        """With an injected ``now``, a task failed exactly 30 min ago (the
        boundary, inclusive) gets a reminder, and the
        ``failure_reminder_sent_at`` stamp equals the frozen moment —
        proving the stamp derives from the injected ``now``, not
        ``timezone.now()``."""
        # Truncate microseconds so the isoformat round-trips exactly.
        frozen = timezone.now().replace(microsecond=0)
        _set_metadata(
            self.task,
            "failure_notified_at",
            (frozen - self.reminder_threshold).isoformat(),
        )
        sent = self.scan_for_failed_reminders(now=frozen)
        self.assertEqual(sent, 1)
        self.task.refresh_from_db()
        self.assertEqual(
            self.task.metadata["failure_reminder_sent_at"],
            frozen.isoformat(),
        )
        self.assertTrue(
            Notification.objects.filter(
                task=self.task, notification_type="task_failed_reminder"
            ).exists()
        )

    def test_reminder_skipped_one_second_before_threshold_frozen(self):
        """29:59 before the frozen clock — one second shy of the threshold —
        the beat job sends nothing and stamps nothing. This pins the
        boundary from below without relying on wall-clock drift."""
        frozen = timezone.now().replace(microsecond=0)
        _set_metadata(
            self.task,
            "failure_notified_at",
            (frozen - self.reminder_threshold + timedelta(seconds=1)).isoformat(),
        )
        sent = self.scan_for_failed_reminders(now=frozen)
        self.assertEqual(sent, 0)
        self.task.refresh_from_db()
        self.assertNotIn("failure_reminder_sent_at", self.task.metadata)
        self.assertFalse(
            Notification.objects.filter(
                task=self.task, notification_type="task_failed_reminder"
            ).exists()
        )
