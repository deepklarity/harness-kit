"""Fable task #359 — task page readability.

The failure banner on a FAILED task must always describe the failure that
actually parked it. Today:

  * The dispatch gate (no worktree, no opt-in) writes the metadata.
  * The execution fallback (odin died without reporting) writes it.
  * The stale-execution recovery writes it.
  * **The review/reflection cap (3 strikes → FAILED) does NOT write it.**
    The previous failure's metadata is left in place, so the banner lies.

This test pins the contract for every FAILED transition path, the
per-class suggested-action table, and the human-sentence formatter that
turns a raw reason into a sentence a person can say out loud.

Failing-first gate: every test in this module must fail BEFORE the
implementation lands (the reflection-cap path is the broken one).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import override_settings

from tests.base import APITestCase
from tasks.models import (
    ReflectionReport,
    ReflectionStatus,
    TaskComment,
    TaskHistory,
    TaskStatus,
)
from tasks.failure_messages import (
    SUGGESTED_ACTIONS,
    format_reason_for_humans,
    humanize_failure_reason,
    suggested_action_for_metadata,
)


# ── 1. Reflection-cap (3-strike) writes failure metadata ───────────────────


class ReflectionCapWritesFailureMetadata(APITestCase):
    """The review/reflection cap path is the one that DOES NOT write the
    failure metadata today. After three NEEDS_WORK/FAIL reflections the
    task flips to FAILED — the banner must describe that fact, not the
    stale failure from an earlier execution.

    The metadata block is what every downstream surface reads
    (frontend banner, inbox, failed_reminder, failure-policy audit).
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def _completed(self, task, verdict="NEEDS_WORK"):
        return ReflectionReport.objects.create(
            task=task,
            reviewer_agent="claude",
            reviewer_model="claude-sonnet-4-5-20250929",
            requested_by="system@taskit",
            status=ReflectionStatus.COMPLETED,
            verdict=verdict,
            verdict_summary="Previous attempt.",
        )

    def _running(self, task):
        return ReflectionReport.objects.create(
            task=task,
            reviewer_agent="claude",
            reviewer_model="claude-sonnet-4-5-20250929",
            requested_by="system@taskit",
            status=ReflectionStatus.RUNNING,
        )

    def test_third_needs_work_writes_review_cap_metadata(self):
        """3rd NEEDS_WORK → FAILED writes last_failure_type=review_cap."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        self._completed(task, "NEEDS_WORK")
        self._completed(task, "NEEDS_WORK")
        report = self._running(task)

        resp = self.client.patch(
            f"/reflections/{report.id}/",
            {"status": "COMPLETED", "verdict": "NEEDS_WORK",
             "verdict_summary": "Code quality still off."},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        task.refresh_from_db()
        meta = task.metadata or {}
        self.assertEqual(meta.get("last_failure_type"), "review_cap")
        self.assertEqual(meta.get("failure_class"), "review_cap")
        self.assertEqual(meta.get("last_failure_origin"), "taskit_views")

    def test_third_needs_work_writes_human_readable_reason(self):
        """The reason must be a sentence a person can say out loud."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        self._completed(task, "NEEDS_WORK")
        self._completed(task, "NEEDS_WORK")
        report = self._running(task)

        self.client.patch(
            f"/reflections/{report.id}/",
            {"status": "COMPLETED", "verdict": "NEEDS_WORK",
             "verdict_summary": "Code quality still off."},
            format="json",
        )

        task.refresh_from_db()
        reason = (task.metadata or {}).get("last_failure_reason", "")
        # Spoken-sentence style: not all-caps log lines, no JSON dump,
        # no key:value fields. Mentions the reviewer (so a human knows
        # where to look next).
        self.assertNotIn(":", reason.split(".")[0])  # no key:value first clause
        self.assertNotIn(" requeued", reason)         # not a log line
        self.assertNotIn("path", reason)             # not a system fingerprint
        self.assertTrue(len(reason) > 30)
        self.assertIn("review", reason.lower())

    def test_third_needs_work_overwrites_prior_failure_metadata(self):
        """The 3-strike FAILED must NOT inherit a stale failure_type from
        an earlier execution — the banner must describe the cap, not the
        last crash. This is the #356 bug."""
        task = self.make_task(
            self.board,
            status=TaskStatus.REVIEW,
            metadata={
                "last_failure_type": "stale_execution",
                "last_failure_reason": "Worker died on a previous attempt.",
                "last_failure_origin": "taskit_dag_executor",
                "failure_class": "stale_execution",
            },
        )
        self._completed(task, "NEEDS_WORK")
        self._completed(task, "NEEDS_WORK")
        report = self._running(task)

        self.client.patch(
            f"/reflections/{report.id}/",
            {"status": "COMPLETED", "verdict": "NEEDS_WORK",
             "verdict_summary": "Stale."},
            format="json",
        )

        task.refresh_from_db()
        meta = task.metadata or {}
        # The stale_execution stamp is replaced by the cap's stamp.
        self.assertEqual(meta.get("last_failure_type"), "review_cap")
        self.assertEqual(meta.get("failure_class"), "review_cap")
        self.assertEqual(meta.get("last_failure_origin"), "taskit_views")
        # The reason doesn't lie about a 3-day-old stale reap.
        self.assertNotIn("worker died", (meta.get("last_failure_reason") or "").lower())

    def test_third_fail_writes_review_cap_metadata(self):
        """3rd FAIL verdict (not just NEEDS_WORK) → same metadata."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        self._completed(task, "FAIL")
        self._completed(task, "FAIL")
        report = self._running(task)

        self.client.patch(
            f"/reflections/{report.id}/",
            {"status": "COMPLETED", "verdict": "FAIL",
             "verdict_summary": "Test still failing."},
            format="json",
        )

        task.refresh_from_db()
        meta = task.metadata or {}
        self.assertEqual(meta.get("last_failure_type"), "review_cap")
        self.assertEqual(meta.get("failure_class"), "review_cap")

    def test_third_needs_work_suggested_action_is_read_review(self):
        """The 3-strike cap suggests reading the reviewer's finding, not
        re-dispatching the same agent (the #356 lie)."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        self._completed(task, "NEEDS_WORK")
        self._completed(task, "NEEDS_WORK")
        report = self._running(task)

        self.client.patch(
            f"/reflections/{report.id}/",
            {"status": "COMPLETED", "verdict": "NEEDS_WORK",
             "verdict_summary": "x"},
            format="json",
        )

        task.refresh_from_db()
        action = suggested_action_for_metadata(task.metadata or {})
        self.assertTrue(len(action) > 10)
        # Plain English about reading the reviewer's note — NOT "requeue".
        self.assertNotIn("requeue", action.lower())
        self.assertNotIn("re-dispatch", action.lower())
        self.assertIn("review", action.lower())

    def test_third_needs_work_audit_comment_is_plain_english(self):
        """The status_update comment posted at the cap must be a
        human sentence, not a log line. Today it says:
            'Task failed after 3 reflection attempts without passing.'
        which is borderline; the new comment leads with the *why* and the
        *next step*."""
        task = self.make_task(self.board, status=TaskStatus.REVIEW)
        self._completed(task, "NEEDS_WORK")
        self._completed(task, "NEEDS_WORK")
        report = self._running(task)

        self.client.patch(
            f"/reflections/{report.id}/",
            {"status": "COMPLETED", "verdict": "NEEDS_WORK",
             "verdict_summary": "x"},
            format="json",
        )

        comment = TaskComment.objects.filter(
            task=task, author_email="system@taskit",
        ).last()
        self.assertIsNotNone(comment)
        # No log-style key:value noise, no path-shaped tokens.
        self.assertNotIn("Reason:", comment.content)
        self.assertNotIn("Failure type:", comment.content)
        # Mentions reviewer + read-the-finding next step.
        self.assertIn("review", comment.content.lower())


# ── 2. Suggested-action table is complete ─────────────────────────────────


class SuggestedActionsTable(APITestCase):
    """Every failure class must have an honest, one-line suggested action
    in plain English. The mapping sits next to the policy table so the
    two can't drift."""

    def setUp(self):
        super().setUp()

    def test_every_class_in_taxonomy_has_an_action(self):
        from tasks.failure_tagger import FAILURE_CLASSES
        missing = []
        for cls in FAILURE_CLASSES:
            action = SUGGESTED_ACTIONS.get(cls)
            if not action or not action.strip():
                missing.append(cls)
        self.assertEqual(missing, [], f"Classes missing suggested action: {missing}")

    def test_review_cap_suggests_reading_reviewer(self):
        """The 3-strike cap suggests reading the reviewer's finding —
        not re-dispatching (which is what produced the #356 banner lie)."""
        self.assertIn("review", SUGGESTED_ACTIONS["review_cap"].lower())
        self.assertNotIn("requeue", SUGGESTED_ACTIONS["review_cap"].lower())
        self.assertNotIn("re-dispatch", SUGGESTED_ACTIONS["review_cap"].lower())

    def test_quota_exhaustion_suggests_wait_or_reassign(self):
        """Quota failure suggests reassign / wait — not a raw requeue."""
        action = SUGGESTED_ACTIONS["quota_exhaustion"].lower()
        self.assertTrue(
            "reassign" in action or "wait" in action or "different" in action,
            f"quota_exhaustion action should mention reassign/wait; got: {action!r}",
        )

    def test_env_missing_suggests_fix_credentials(self):
        """API key missing / auth 401 → operator must fix env."""
        action = SUGGESTED_ACTIONS["env_missing"].lower()
        self.assertTrue(
            "key" in action or "credential" in action or "auth" in action
            or "environment" in action,
            f"env_missing should mention credentials/auth; got: {action!r}",
        )

    def test_actions_are_short_sentences(self):
        """Each action is one sentence, under ~140 chars — the banner
        shows it verbatim."""
        for cls, action in SUGGESTED_ACTIONS.items():
            self.assertLessEqual(
                len(action), 160,
                f"{cls} action is too long ({len(action)}): {action!r}",
            )
            # Plain English: no log-style all-caps, no key:value, no path tokens.
            self.assertNotIn(":", action, f"{cls}: no key:value noise")
            self.assertNotIn("requeued", action.lower(), f"{cls}: log-speak")


class SuggestedActionForMetadata(APITestCase):
    """Helper picks the action from a task's metadata block."""

    def test_returns_action_for_known_class(self):
        action = suggested_action_for_metadata({"failure_class": "env_missing"})
        self.assertEqual(action, SUGGESTED_ACTIONS["env_missing"])

    def test_returns_action_for_review_cap(self):
        action = suggested_action_for_metadata({"failure_class": "review_cap"})
        self.assertEqual(action, SUGGESTED_ACTIONS["review_cap"])

    def test_falls_back_to_unknown_for_missing_class(self):
        action = suggested_action_for_metadata({})
        self.assertEqual(action, SUGGESTED_ACTIONS["unknown"])

    def test_falls_back_to_unknown_for_garbage_class(self):
        action = suggested_action_for_metadata({"failure_class": "made_up"})
        self.assertEqual(action, SUGGESTED_ACTIONS["unknown"])


# ── 3. Human-sentence reason formatter ────────────────────────────────────


class HumanizeFailureReason(APITestCase):
    """The banner shows a sentence a person can say out loud. Machine
    fields (type, origin, pid) belong in metadata for the trace viewer,
    not in the user-facing banner."""

    def test_format_reason_for_humans_drops_key_value_pairs(self):
        text = "Failure type: stale_execution\nReason: worker died after 300s\nOrigin: taskit_dag_executor"
        sentence = format_reason_for_humans(text)
        self.assertNotIn("Failure type:", sentence)
        self.assertNotIn("Reason:", sentence)
        self.assertNotIn("Origin:", sentence)

    def test_humanize_failure_reason_for_dispatch_gate(self):
        """Dispatch gate (no worktree) gets a spoken sentence."""
        sentence = humanize_failure_reason(
            "No task worktree could be created (no spec branch or worktree "
            "creation failed) and the board does not allow project-root "
            "execution.",
            failure_type="missing_worktree",
            failure_origin="taskit_dag_executor",
        )
        self.assertGreater(len(sentence), 20)
        # Mentions the actual cause in plain words.
        self.assertTrue(
            "worktree" in sentence.lower() or "branch" in sentence.lower()
        )

    def test_humanize_failure_reason_for_timeout(self):
        """Timeout reason should read as a sentence."""
        sentence = humanize_failure_reason(
            "Task execution timed out",
            failure_type="timeout",
            failure_origin="taskit_dag_executor",
        )
        self.assertGreater(len(sentence), 10)
        self.assertNotIn(":", sentence.split(".")[0])
        self.assertNotIn("taskit_dag_executor", sentence)


# ── 4. Dispatch-gate path: already writes metadata (regression guard) ────


class DispatchGateWritesMetadata(APITestCase):
    """Pin the existing contract: the dispatch gate that marks a task
    FAILED also writes the failure metadata + suggested action."""

    def setUp(self):
        super().setUp()
        from tasks.dag_executor import poll_and_execute
        self.poll = poll_and_execute
        self.board = self.make_board()
        self.user = self.make_user()

    @patch("tasks.dag_executor.execute_single_task")
    def test_dispatch_gate_failure_classifies(self, mock_exec):
        """A no-spec, no-opt-in board: task FAILED with worktree_isolation
        class + a suggested action that's about worktrees / opt-in."""
        mock_exec.delay.return_value = MagicMock(id="x")
        task = self.make_task(
            self.board,
            status=TaskStatus.IN_PROGRESS,
            assignee=self.user,
            depends_on=[],
        )

        self.poll()

        task.refresh_from_db()
        meta = task.metadata or {}
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(meta.get("last_failure_type"), "missing_worktree")
        self.assertEqual(meta.get("failure_class"), "worktree_isolation")
        # Suggested action now points the operator at worktree / opt-in.
        action = suggested_action_for_metadata(meta)
        self.assertGreater(len(action), 5)
        self.assertNotIn("requeue", action.lower())
        self.assertNotIn("re-dispatch", action.lower())


# ── 5. Serializer exposes the human-readable fields ───────────────────────


class SerializerExposesFailureReadabilityFields(APITestCase):
    """The TaskSerializer must expose a plain suggested-action string and
    a human-readable reason sentence so the frontend doesn't reinvent
    them. Pin both ends of the API."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def test_failed_task_exposes_failure_suggested_action(self):
        task = self.make_task(
            self.board,
            status=TaskStatus.FAILED,
            metadata={
                "failure_class": "review_cap",
                "last_failure_type": "review_cap",
                "last_failure_reason": "The reviewer rejected the work three times.",
                "last_failure_origin": "taskit_views",
            },
        )
        resp = self.client.get(f"/tasks/{task.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("failure_suggested_action", resp.data)
        self.assertEqual(
            resp.data["failure_suggested_action"],
            SUGGESTED_ACTIONS["review_cap"],
        )

    def test_failed_task_exposes_failure_human_reason(self):
        """The reason field comes back as a sentence, not a log line."""
        task = self.make_task(
            self.board,
            status=TaskStatus.FAILED,
            metadata={
                "failure_class": "review_cap",
                "last_failure_type": "review_cap",
                "last_failure_reason": "The reviewer rejected the work three times.",
                "last_failure_origin": "taskit_views",
            },
        )
        resp = self.client.get(f"/tasks/{task.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("failure_human_reason", resp.data)
        reason = resp.data["failure_human_reason"]
        # Not a log line: no key:value noise.
        self.assertNotIn("Failure type:", reason)
        self.assertNotIn("Origin:", reason)
        self.assertGreater(len(reason), 5)

    def test_non_failed_task_returns_empty_action(self):
        """A TODO/IN_PROGRESS task has no suggested action — the banner
        doesn't render in the first place, but the field must not lie."""
        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        resp = self.client.get(f"/tasks/{task.id}/")
        self.assertEqual(resp.status_code, 200)
        # Either empty string or omitted entirely — must not have a stale
        # suggested action leaking through.
        self.assertFalse(resp.data.get("failure_suggested_action", ""))


# ── 6. Frontend TaskActionHub shows per-class action (vitest, sibling) ────
#
# Frontend coverage lives in taskit-frontend/src/components/TaskActionHub.test.tsx.
# These Python tests pin the BACKEND half — the API returns the right string
# per class, so the frontend's render-the-string tests stay short.