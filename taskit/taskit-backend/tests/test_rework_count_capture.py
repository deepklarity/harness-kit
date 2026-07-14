"""Tests for the rework_count capture on task.metadata.

Origin: W3.17 — the analytics page exposes a "rework-cycle waste" stat,
which requires a per-task rework-round counter that survives across the
different rework entry points (F45 NEEDS_WORK continuity, F159 quota
reassignment, infra auto-redispatch). The counter lives at
``task.metadata["rework_count"]`` and is bumped exactly once per retry,
regardless of which path fired the retry.

Each test class pins a single entry point so a future regression that
breaks one path (and not the others) is caught by name.

Non-visual — no browser/screenshots needed.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

import django  # noqa: E402

django.setup()

from datetime import datetime, timedelta, timezone  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402

from tests.base import APITestCase  # noqa: E402
from tasks.models import (  # noqa: E402
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


def _make_agent_user(name="claude", email="claude@odin.agent",
                     available_models=None):
    user, _ = User.objects.get_or_create(
        email=email,
        defaults={
            "name": name,
            "role": UserRole.AGENT,
            "is_active": True,
            "available_models": available_models or ["claude-sonnet-4-5"],
        },
    )
    return user


class ReworkCountContinuity(APITestCase):
    """F45 NEEDS_WORK continuity must bump rework_count on every retry.

    Pin: the F45 continuity path (no quota reassignment, no infra class)
    writes task.metadata["rework_count"] += 1 per NEEDS_WORK retry. The
    counter survives across multiple NEEDS_WORK reflections on the same
    task so the analytics page can show rework-cycle waste per task.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.claude = _make_agent_user()
        BoardMembership.objects.create(board=self.board, user=self.claude)

    def _create_running_report(self, task):
        return ReflectionReport.objects.create(
            task=task,
            reviewer_agent="claude",
            reviewer_model="claude-sonnet-4-5",
            requested_by="system@taskit",
            status=ReflectionStatus.RUNNING,
        )

    def _complete_with_needs_work(self, report, verdict_summary="Needs improvement."):
        return self.client.patch(
            f"/reflections/{report.id}/",
            {
                "status": "COMPLETED",
                "verdict": "NEEDS_WORK",
                "verdict_summary": verdict_summary,
            },
            format="json",
        )

    @patch("tasks.execution.get_strategy")
    def test_first_rework_bumps_count_to_one(self, mock_get_strategy):
        """The first NEEDS_WORK retry sets rework_count == 1."""
        mock_get_strategy.return_value = MagicMock()
        task = self.make_task(
            self.board,
            title="Continuity rework #1",
            status=TaskStatus.REVIEW,
            assignee=self.claude,
            model_name="claude-sonnet-4-5",
        )
        report = self._create_running_report(task)
        resp = self._complete_with_needs_work(report)
        self.assertEqual(resp.status_code, 200, resp.data)

        task.refresh_from_db()
        self.assertEqual(
            (task.metadata or {}).get("rework_count"),
            1,
            "F45 continuity must bump rework_count to 1 on the first retry.",
        )

    @patch("tasks.execution.get_strategy")
    def test_repeated_rework_increments_cumulatively(self, mock_get_strategy):
        """Each subsequent NEEDS_WORK retry increments, not resets."""
        mock_get_strategy.return_value = MagicMock()

        task = self.make_task(
            self.board,
            title="Multiple reworks",
            status=TaskStatus.REVIEW,
            assignee=self.claude,
            model_name="claude-sonnet-4-5",
        )

        # First rework — count should become 1.
        r1 = self._create_running_report(task)
        resp = self._complete_with_needs_work(r1, "Attempt 1: needs work.")
        self.assertEqual(resp.status_code, 200, resp.data)
        task.refresh_from_db()
        self.assertEqual((task.metadata or {}).get("rework_count"), 1)

        # The view auto-advances the task to IN_PROGRESS after NEEDS_WORK.
        # Force it back to REVIEW so the second report fires the rework
        # branch again (only REVIEW status triggers the auto-rework).
        task.status = TaskStatus.REVIEW
        task.save(update_fields=["status"])

        # Second rework — count should become 2.
        r2 = self._create_running_report(task)
        resp = self._complete_with_needs_work(r2, "Attempt 2: still needs work.")
        self.assertEqual(resp.status_code, 200, resp.data)
        task.refresh_from_db()
        self.assertEqual(
            (task.metadata or {}).get("rework_count"),
            2,
            "Second NEEDS_WORK retry must increment rework_count to 2, "
            "not overwrite it back to 1.",
        )

    @patch("tasks.execution.get_strategy")
    def test_rework_count_survives_alongside_other_metadata(self, mock_get_strategy):
        """The bump must not destroy the existing last_rework_* keys."""
        mock_get_strategy.return_value = MagicMock()
        task = self.make_task(
            self.board,
            title="Preserve other metadata",
            status=TaskStatus.REVIEW,
            assignee=self.claude,
            model_name="claude-sonnet-4-5",
        )
        report = self._create_running_report(task)
        self._complete_with_needs_work(report)
        task.refresh_from_db()
        meta = task.metadata or {}
        self.assertEqual(meta.get("rework_count"), 1)
        self.assertEqual(meta.get("last_rework_reason"), "rework_continuity")
        self.assertIsNotNone(
            meta.get("last_rework_at"),
            "last_rework_at must still be set (F45 mandate preserved).",
        )


class ReworkCountQuota(APITestCase):
    """Quota-driven reassignment must also bump rework_count.

    Pin: when the F45 quota path reassigns an agent, the continuity
    helper short-circuits (because assignee/model moved). The quota
    reassignment itself must still bump rework_count, otherwise the
    analytics page under-counts retries on quota-exhausted boards.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        # Use a real active agent so the verified-exhausted path actually
        # reassigns. ``_find_alternative_agent`` filters by the active
        # lineup in agent_models.json — picking a non-listed name would
        # short-circuit to "no alternative agent available" and skip the
        # bump branch this test pins.
        self.exhausted_agent = _make_agent_user(
            name="glm",
            email="glm@odin.agent",
            available_models=["glm-4.6"],
        )
        self.alternative_agent = _make_agent_user(
            name="claude",
            email="claude@odin.agent",
            available_models=["claude-sonnet-4-5"],
        )
        BoardMembership.objects.create(board=self.board, user=self.exhausted_agent)
        BoardMembership.objects.create(board=self.board, user=self.alternative_agent)

    def _complete_with_quota_failure(self, report):
        return self.client.patch(
            f"/reflections/{report.id}/",
            {
                "status": "COMPLETED",
                "verdict": "NEEDS_WORK",
                "verdict_summary": "Quota exhausted: 429 too many requests, out of quota.",
            },
            format="json",
        )

    @patch("tasks.execution.get_strategy")
    def test_quota_reassign_bumps_rework_count(self, mock_get_strategy):
        """Quota path with a real alternative agent must bump rework_count.

        The keyword check inside ``_is_quota_failure`` matches our
        verdict_summary, and the harness_usage_status check is forced to
        EXHAUSTED via patching so the verified path fires. The alternative
        agent is on the board so reassignment actually happens.
        """
        mock_get_strategy.return_value = MagicMock()

        task = self.make_task(
            self.board,
            title="Quota retry",
            status=TaskStatus.REVIEW,
            assignee=self.exhausted_agent,
            model_name="claude-sonnet-4-5",
        )
        report = ReflectionReport.objects.create(
            task=task,
            reviewer_agent="claude",
            reviewer_model="claude-sonnet-4-5",
            requested_by="system@taskit",
            status=ReflectionStatus.RUNNING,
        )

        # Force the verified-exhausted path. Without the patch the checker
        # returns UNAVAILABLE in tests (no harness_usage_status wiring)
        # and the function falls through to the legacy "unverified" branch.
        # Either branch reassigns, but the patch pins the verified path so
        # the test is unambiguous about which contract fires.
        fake_exhausted = MagicMock()
        fake_exhausted.state = "exhausted"
        fake_exhausted.detail = "usage at 100%"
        with patch("tasks.views._check_provider_usage", return_value=fake_exhausted):
            resp = self._complete_with_quota_failure(report)

        self.assertEqual(resp.status_code, 200, resp.data)
        task.refresh_from_db()
        self.assertEqual(
            (task.metadata or {}).get("rework_count"),
            1,
            "Quota reassignment must bump rework_count to 1 so the "
            "analytics rework-cycle stat counts quota retries too.",
        )


class ReworkCountInfraAutoRedispatch(APITestCase):
    """Infra-class auto-redispatch must increment rework_count via the F45 helper.

    The infra path in dag_executor.py stamps ``last_rework_reason =
    'infra_auto_redispatch'`` and then calls ``_record_rework_continuity``
    with ``suppress_comment=True``. The continuity helper bumps
    ``rework_count`` because the agent+model are preserved. This test
    pins that behavior end-to-end at the helper level (avoids the full
    subprocess path).
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.claude = _make_agent_user()
        BoardMembership.objects.create(board=self.board, user=self.claude)

    def test_continuity_helper_bumps_rework_count(self):
        """Direct call to _record_rework_continuity bumps rework_count by 1."""
        from tasks.views import _record_rework_continuity

        task = self.make_task(
            self.board,
            title="Infra retry direct call",
            status=TaskStatus.IN_PROGRESS,
            assignee=self.claude,
            model_name="claude-sonnet-4-5",
        )

        # Initial state — no rework_count yet.
        self.assertNotIn("rework_count", (task.metadata or {}))

        # First infra retry.
        _record_rework_continuity(
            task, self.claude.id, "claude-sonnet-4-5", suppress_comment=True,
        )
        task.refresh_from_db()
        self.assertEqual(
            (task.metadata or {}).get("rework_count"),
            1,
            "Infra auto-redispatch path bumps rework_count via the F45 helper.",
        )
        self.assertEqual(
            (task.metadata or {}).get("last_rework_reason"),
            "rework_continuity",
            "Infra path goes through continuity helper, so reason is continuity.",
        )

        # Second infra retry — count must increment, not reset.
        _record_rework_continuity(
            task, self.claude.id, "claude-sonnet-4-5", suppress_comment=True,
        )
        task.refresh_from_db()
        self.assertEqual(
            (task.metadata or {}).get("rework_count"),
            2,
            "Second infra retry must increment to 2, not overwrite to 1.",
        )
