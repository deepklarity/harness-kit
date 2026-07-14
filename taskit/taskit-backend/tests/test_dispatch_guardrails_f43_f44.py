"""F43/F44 dispatch guardrail regression tests.

Origin: docs/fable_roadmap/OPERATIONS.md — F43 (silent unknown-field drops) +
F44 (silent project-root execution). These tests pin the contract the
operator's brief mandates; if any of them fails the regression that produced
task #114 is back.

Coverage (4 mandated regression tests):

  1. test_create_task_rejects_unknown_fields_with_400
     POST /tasks/ with `spec` and `assignee` (the F43 wrong field names) MUST
     return 400 listing the rejected names, not 201 with silently-dropped
     fields. Without this, task #114's evidence path repeats: operators see
     a "successful" task with no spec/assignee and no error.

  2. test_unassigned_task_with_suggested_agent_is_auto_assigned
     A task that hits the dispatch gate without an assignee but with
     `metadata.suggested_agent` MUST be auto-assigned from the active lineup
     instead of being skipped silently. Default First (F43) — the operator's
     plan said "claude", the poller should honor it.

  3. test_no_spec_task_dispatch_halts_without_opt_in
     A task with no spec (so no worktree possible) on a board that has NOT
     opted into project-root execution MUST transition to FAILED with a
     visible `dispatch_blocked_reason=no_worktree_no_optin` stamp, not be
     silently dispatched into the project root. Without this, task #114's
     isolation breach (an agent writing into the operator's working dir) is
     back.

  4. test_patch_spec_id_writes_the_fk
     PATCH /tasks/<id>/ with `spec_id` MUST persist the FK and record the
     change in TaskHistory. Without this, the only way to wire a task to a
     spec after creation was direct DB poking — which the operator cannot do.

Non-visual — no browser/screenshots needed.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import patch, MagicMock
from pathlib import Path

from tests.base import APITestCase
from tasks.models import (
    BoardMembership,
    Task,
    TaskComment,
    TaskHistory,
    TaskStatus,
    User,
)
from tasks.dag_executor import poll_and_execute


class StrictUnknownFieldsRegression(APITestCase):
    """F43 regression 1/4: unknown POST fields must return 400 with the names.

    Task #114 was created via POST /tasks/ with ``"spec": 49`` and
    ``"assignee": 5``. Both were silently dropped (DRF default), so the
    resulting task had no spec and no assignee — the dispatch guardrails
    couldn't route it, and the operator saw "success" while the task was
    half-configured.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def test_create_task_rejects_unknown_fields_with_400(self):
        """POST with `spec` and `assignee` (wrong field names) → 400, not silent drop."""
        spec = self.make_spec(self.board)
        user = self.make_user()
        resp = self.client.post("/tasks/", {
            "board_id": self.board.id,
            "title": "Wrong fields",
            "created_by": "alice@test.com",
            # Both wrong names from the F43 evidence.
            "spec": spec.id,
            "assignee": user.id,
        }, format="json")

        self.assertEqual(resp.status_code, 400)
        body = resp.data
        # The mixin reports `unknown_fields` so clients can fix their payloads.
        self.assertIn("unknown_fields", body)
        unknown = body["unknown_fields"]
        self.assertIn("spec", unknown)
        self.assertIn("assignee", unknown)
        # No task was created — the rejected payload short-circuits before insert.
        self.assertFalse(Task.objects.filter(title="Wrong fields").exists())

    def test_create_task_accepts_correct_field_names(self):
        """POST with the correct names (`spec_id`, `assignee_id`) still works."""
        spec = self.make_spec(self.board)
        user = self.make_user()
        resp = self.client.post("/tasks/", {
            "board_id": self.board.id,
            "title": "Right fields",
            "created_by": "alice@test.com",
            "spec_id": spec.id,
            "assignee_id": user.id,
        }, format="json")

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["title"], "Right fields")
        self.assertEqual(resp.data["spec_odin_id"], spec.odin_id)
        self.assertEqual(resp.data["assignee"]["id"], user.id)


class DefaultFirstAutoAssignRegression(APITestCase):
    """F43 regression 2/4: unassigned task with suggested_agent → auto-assign.

    Default First principle: if the operator's plan said "claude", an unassigned
    task that hits the poll should be assigned claude rather than silently
    skipped. The operator's intent on the spec is the source of truth; manual
    intervention only when they want something different.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board(allow_project_root_execution=True)
        # Create the User row that `_auto_assign_from_suggested` resolves to.
        # Identity emails follow `{name}@odin.agent` or `{name}+{model}@odin.agent.
        self.claude_user = self.make_user(
            name="Claude",
            email="claude@odin.agent",
        )
        # W10.4: the auto-assign gate requires the agent to be enrolled on
        # the board's roster; without this membership the guard refuses
        # with the named-error path. The poll-and-execute tests exercise
        # the happy path so the agent is enrolled up front.
        BoardMembership.objects.create(board=self.board, user=self.claude_user)

    @patch("tasks.dag_executor.execute_single_task")
    def test_unassigned_task_with_suggested_agent_is_auto_assigned(self, mock_exec):
        """Unassigned IN_PROGRESS task with suggested_agent gets the agent assigned."""
        mock_exec.delay.return_value = MagicMock(id="fake-celery-auto")
        task = self.make_task(
            self.board,
            title="Auto-assigned task",
            status=TaskStatus.IN_PROGRESS,
            assignee=None,
            depends_on=[],
            metadata={"suggested_agent": "claude"},
        )

        poll_and_execute()

        task.refresh_from_db()
        # The poller populated the assignee (Default First).
        self.assertEqual(task.assignee_id, self.claude_user.id)
        # Audit trail: a TaskHistory row records the auto-assignment.
        history = TaskHistory.objects.filter(
            task=task, field_name="assignee",
            changed_by="odin+dag-executor@system",
        )
        self.assertTrue(history.exists())
        latest = history.latest("changed_at")
        self.assertEqual(latest.new_value, str(self.claude_user.id))
        # A status_update comment narrates the auto-assignment.
        comment = TaskComment.objects.filter(task=task).latest("id")
        self.assertIn("Auto-assigned from metadata.suggested_agent", comment.content)
        self.assertIn("claude", comment.content)
        # Board membership gets ensured so notifications see the new agent.
        self.assertTrue(
            BoardMembership.objects.filter(
                board=self.board, user=self.claude_user,
            ).exists()
        )
        # And the dispatch actually fires.
        mock_exec.delay.assert_called_once()

    @patch("tasks.dag_executor.execute_single_task")
    def test_unassigned_task_without_suggested_agent_is_skipped(self, mock_exec):
        """Without suggested_agent, no auto-assign — stays skipped (loud)."""
        mock_exec.delay.return_value = MagicMock(id="fake-celery-noassign")
        task = self.make_task(
            self.board,
            status=TaskStatus.IN_PROGRESS,
            assignee=None,
            depends_on=[],
            metadata={},
        )

        poll_and_execute()

        task.refresh_from_db()
        self.assertIsNone(task.assignee_id)
        # Loud skip reason visible on metadata (F43).
        self.assertEqual(task.metadata.get("dispatch_blocked_reason"), "no_assignee")
        mock_exec.delay.assert_not_called()

    @patch("tasks.dag_executor.execute_single_task")
    def test_inactive_suggested_agent_does_not_auto_assign(self, mock_exec):
        """suggested_agent pointing at a retired agent does NOT auto-assign."""
        mock_exec.delay.return_value = MagicMock(id="fake-celery-retired")
        # gemini was retired in F45 — must not be assignable here.
        task = self.make_task(
            self.board,
            status=TaskStatus.IN_PROGRESS,
            assignee=None,
            depends_on=[],
            metadata={"suggested_agent": "gemini"},
        )

        poll_and_execute()

        task.refresh_from_db()
        self.assertIsNone(task.assignee_id)
        self.assertEqual(task.metadata.get("dispatch_blocked_reason"), "no_assignee")
        mock_exec.delay.assert_not_called()


class NoWorktreeNoOptinRegression(APITestCase):
    """F44 regression 3/4: spec-less task on non-opt-in board → loud FAIL.

    Task #114 evidence: a task with no spec reached the dispatch gate. The
    old code skipped worktree creation, fell through to ``odin exec`` in the
    board's working_dir (the operator's repo root), and committed access to
    the operator's branch. The new gate: unless the board has
    ``allow_project_root_execution=True``, the task FAILS with a visible
    reason instead of silently executing.
    """

    def setUp(self):
        super().setUp()
        # Default board — allow_project_root_execution is False.
        self.board = self.make_board()
        self.user = self.make_user()

    @patch("tasks.dag_executor.execute_single_task")
    def test_no_spec_task_dispatch_halts_without_opt_in(self, mock_exec):
        """No spec + no opt-in → FAILED with no_worktree_no_optin, no exec fired."""
        mock_exec.delay.return_value = MagicMock(id="fake-celery-blocked")
        task = self.make_task(
            self.board,
            title="Spec-less task",
            status=TaskStatus.IN_PROGRESS,
            assignee=self.user,
            depends_on=[],
        )

        poll_and_execute()

        task.refresh_from_db()
        # Loud failure, not silent execution.
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(
            task.metadata.get("dispatch_blocked_reason"),
            "no_worktree_no_optin",
        )
        self.assertEqual(task.metadata.get("last_failure_type"), "missing_worktree")
        self.assertEqual(
            task.metadata.get("last_failure_origin"),
            "taskit_dag_executor",
        )
        # No agent was launched into the project root.
        mock_exec.delay.assert_not_called()
        # A status_update comment narrates the halt.
        comment = TaskComment.objects.filter(task=task).latest("id")
        self.assertIn("no worktree", comment.content.lower())
        self.assertIn("missing_worktree", comment.content)
        # History records the transition IN_PROGRESS → FAILED.
        latest = TaskHistory.objects.filter(
            task=task, field_name="status",
        ).latest("changed_at")
        self.assertEqual(latest.old_value, TaskStatus.IN_PROGRESS)
        self.assertEqual(latest.new_value, TaskStatus.FAILED)

    @patch("tasks.dag_executor.execute_single_task")
    def test_no_spec_task_with_opt_in_dispatches_in_project_root(self, mock_exec):
        """Opt-in board: the dispatch proceeds, metadata flags project-root use."""
        mock_exec.delay.return_value = MagicMock(id="fake-celery-optin")
        # Re-make the board with the explicit opt-in.
        opt_in_board = self.make_board(name="Opted-in", allow_project_root_execution=True)
        task = self.make_task(
            opt_in_board,
            title="Spec-less with opt-in",
            status=TaskStatus.IN_PROGRESS,
            assignee=self.user,
            depends_on=[],
        )

        poll_and_execute()

        task.refresh_from_db()
        # Opt-in path: dispatch fires, task moves to EXECUTING.
        self.assertEqual(task.status, TaskStatus.EXECUTING)
        self.assertTrue(task.metadata.get("project_root_execution_used"))
        self.assertNotIn(
            "dispatch_blocked_reason",
            (task.metadata or {}),
            "opt-in path must not stamp a dispatch-blocked reason",
        )
        mock_exec.delay.assert_called_once()

    @patch("tasks.dag_executor.execute_single_task")
    def test_worktree_creation_failure_without_opt_in_fails_loudly(self, mock_exec):
        """If worktree creation raises and the board did not opt in → FAIL."""
        mock_exec.delay.return_value = MagicMock(id="fake-celery-wtfail")
        # Has a spec with branch metadata, but the WorktreeManager raises.
        spec = self.make_spec(self.board, odin_id="sp_wtf", metadata={"branch": "spec/sp_wtf"})
        task = self.make_task(
            self.board,
            spec=spec,
            title="Worktree-raise task",
            status=TaskStatus.IN_PROGRESS,
            assignee=self.user,
            depends_on=[],
        )

        with patch(
            "tasks.dag_executor._create_task_worktree",
            side_effect=Exception("git exploded"),
        ):
            poll_and_execute()

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(
            task.metadata.get("dispatch_blocked_reason"),
            "no_worktree_no_optin",
        )
        mock_exec.delay.assert_not_called()


class PatchSpecIdRegression(APITestCase):
    """F43 regression 4/4: PATCH spec_id writes the FK and records history.

    Before this change ``UpdateTaskSerializer`` had no ``spec_id`` field, so
    PATCH /tasks/<id>/ with a spec could not actually wire the task to a
    spec. The only workaround was direct DB writes — not viable from the
    UI. Now PATCH writes the FK, records TaskHistory, and the UI reads it.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.user = self.make_user()
        self.task = self.make_task(self.board, title="Patch me")
        self.spec = self.make_spec(self.board, odin_id="sp_patch")

    def test_patch_spec_id_writes_the_fk(self):
        """PATCH /tasks/<id>/ with spec_id persists the FK and records history."""
        self.assertIsNone(self.task.spec_id, "precondition: task has no spec")

        resp = self.client.patch(
            f"/tasks/{self.task.id}/",
            {
                "spec_id": self.spec.id,
                "updated_by": "alice@test.com",
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        self.task.refresh_from_db()
        # The FK is now set.
        self.assertEqual(self.task.spec_id, self.spec.id)
        # TaskHistory records the change.
        history = TaskHistory.objects.filter(
            task=self.task, field_name="spec_id",
        )
        self.assertTrue(history.exists())
        latest = history.latest("changed_at")
        self.assertEqual(latest.old_value, "")
        self.assertEqual(latest.new_value, str(self.spec.id))
        self.assertEqual(latest.changed_by, "alice@test.com")

    def test_patch_spec_id_rejects_unknown_field_with_400(self):
        """PATCH with the F43 wrong field name `spec` (not `spec_id`) → 400."""
        resp = self.client.patch(
            f"/tasks/{self.task.id}/",
            {
                "spec": self.spec.id,
                "updated_by": "alice@test.com",
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("unknown_fields", resp.data)
        self.assertIn("spec", resp.data["unknown_fields"])
        self.task.refresh_from_db()
        self.assertIsNone(self.task.spec_id, "rejected payload must not write")

    def test_patch_spec_id_to_null_clears_the_fk(self):
        """PATCH spec_id=null clears the spec association."""
        # First, wire the task to the spec.
        self.task.spec = self.spec
        self.task.save(update_fields=["spec"])
        self.assertEqual(self.task.spec_id, self.spec.id)

        resp = self.client.patch(
            f"/tasks/{self.task.id}/",
            {
                "spec_id": None,
                "updated_by": "alice@test.com",
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        self.task.refresh_from_db()
        self.assertIsNone(self.task.spec_id)