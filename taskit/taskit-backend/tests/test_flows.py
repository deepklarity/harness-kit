"""Complex flow tests for taskit — simulates real Odin orchestration scenarios.

These tests go beyond CRUD to cover:
- Full task lifecycle (TODO → IN_PROGRESS → REVIEW → DONE)
- History audit trail integrity
- Board membership auto-management
- Spec cloning with atomic consistency
- Board clear operations
- Label management with history tracking
- Multi-task dependency workflows
"""

from unittest.mock import patch

from .base import APITestCase
from tasks.models import BoardMembership, Task, TaskHistory


# ═══════════════════════════════════════════════════════════════════════
# Task lifecycle & history tracking
# ═══════════════════════════════════════════════════════════════════════


class TestTaskLifecycle(APITestCase):
    """Simulates Odin-driven task progression through status stages."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.user = self.make_user()

    def test_full_lifecycle_todo_to_done(self):
        """A task goes through TODO → IN_PROGRESS → REVIEW → DONE."""
        # Create task via API (records "created" history)
        resp = self.make_task_via_api(self.board, title="Implement auth")
        task_id = resp.data["id"]

        # Assign to user
        self.client.post(f"/tasks/{task_id}/assign/", {
            "assignee_id": self.user.id,
            "updated_by": "lead@test.com",
        }, format="json")

        # Move through statuses
        for new_status in ("IN_PROGRESS", "REVIEW", "DONE"):
            resp = self.client.put(f"/tasks/{task_id}/", {
                "status": new_status,
                "updated_by": "alice@test.com",
            }, format="json")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.data["status"], new_status)

        # Verify complete history trail
        resp = self.client.get(f"/tasks/{task_id}/history/")
        history = self.results(resp)
        field_names = [h["field_name"] for h in history]
        self.assertIn("created", field_names)
        self.assertIn("assignee_id", field_names)
        # Status changed 3 times
        status_changes = [h for h in history if h["field_name"] == "status"]
        self.assertEqual(len(status_changes), 3)

    def test_status_change_records_old_and_new(self):
        task = self.make_task(self.board, status="TODO")
        self.client.put(f"/tasks/{task.id}/", {
            "status": "IN_PROGRESS",
            "updated_by": "alice@test.com",
        }, format="json")

        history = TaskHistory.objects.filter(task=task, field_name="status").first()
        self.assertEqual(history.old_value, "TODO")
        self.assertEqual(history.new_value, "IN_PROGRESS")
        self.assertEqual(history.changed_by, "alice@test.com")

    def test_no_history_when_value_unchanged(self):
        """Updating a field to its current value should not create history."""
        task = self.make_task(self.board, priority="MEDIUM")
        self.client.put(f"/tasks/{task.id}/", {
            "priority": "MEDIUM",
            "updated_by": "alice@test.com",
        }, format="json")

        history = TaskHistory.objects.filter(task=task, field_name="priority")
        self.assertEqual(history.count(), 0)

    def test_multiple_field_changes_in_one_update(self):
        """Updating multiple fields at once records separate history entries."""
        task = self.make_task(self.board, title="Old Title", priority="LOW")
        self.client.put(f"/tasks/{task.id}/", {
            "title": "New Title",
            "priority": "HIGH",
            "updated_by": "alice@test.com",
        }, format="json")

        history = TaskHistory.objects.filter(task=task)
        changed_fields = set(h.field_name for h in history)
        self.assertIn("title", changed_fields)
        self.assertIn("priority", changed_fields)

    def test_execution_result_records_status_and_comment(self):
        """Odin writes execution results via the execution_result endpoint."""
        task = self.make_task(self.board, status="IN_PROGRESS")
        resp = self.client.post(f"/tasks/{task.id}/execution_result/", {
            "execution_result": {
                "success": True,
                "raw_output": "All tests passed.",
                "duration_ms": 5000.0,
                "agent": "mock",
                "metadata": {},
            },
            "status": "DONE",
            "updated_by": "mock@odin.agent",
        }, format="json")
        self.assertEqual(resp.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, "DONE")
        # Verify history tracks the status change
        history_fields = set(
            TaskHistory.objects.filter(task=task).values_list("field_name", flat=True)
        )
        self.assertIn("status", history_fields)
        # Verify comment was created
        from tasks.models import TaskComment
        comments = TaskComment.objects.filter(task=task)
        self.assertEqual(comments.count(), 1)

    @patch("tasks.execution.get_strategy")
    def test_in_progress_triggers_execution_strategy(self, mock_get_strategy):
        """Moving to IN_PROGRESS with an assignee triggers the execution strategy."""
        mock_strategy = mock_get_strategy.return_value
        task = self.make_task(self.board, assignee=self.user)

        self.client.put(f"/tasks/{task.id}/", {
            "status": "IN_PROGRESS",
            "updated_by": "alice@test.com",
        }, format="json")

        mock_strategy.trigger.assert_called_once()
        triggered_task = mock_strategy.trigger.call_args[0][0]
        self.assertEqual(triggered_task.id, task.id)

    @patch("tasks.execution.get_strategy")
    def test_in_progress_no_trigger_without_assignee(self, mock_get_strategy):
        """Moving to IN_PROGRESS without an assignee does NOT trigger execution."""
        mock_strategy = mock_get_strategy.return_value
        task = self.make_task(self.board)  # no assignee

        self.client.put(f"/tasks/{task.id}/", {
            "status": "IN_PROGRESS",
            "updated_by": "alice@test.com",
        }, format="json")

        mock_strategy.trigger.assert_not_called()

    @patch("tasks.execution.get_strategy", return_value=None)
    def test_in_progress_no_strategy_configured(self, mock_get_strategy):
        """Moving to IN_PROGRESS with no strategy configured doesn't crash."""
        task = self.make_task(self.board, assignee=self.user)
        resp = self.client.put(f"/tasks/{task.id}/", {
            "status": "IN_PROGRESS",
            "updated_by": "alice@test.com",
        }, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "IN_PROGRESS")

    @patch("tasks.execution.get_strategy")
    def test_stop_execution_success_moves_to_target_status(self, mock_get_strategy):
        mock_strategy = mock_get_strategy.return_value
        mock_strategy.stop.return_value = {"ok": True, "engine": "local"}
        task = self.make_task(
            self.board,
            status="EXECUTING",
            assignee=self.user,
            metadata={"active_execution": {"run_token": "run_1", "pid": 1234}},
        )

        resp = self.client.post(f"/tasks/{task.id}/stop_execution/", {
            "updated_by": "alice@test.com",
            "target_status": "TODO",
            "reason": "user_drag_stop_confirm",
        }, format="json")
        self.assertEqual(resp.status_code, 200)

        task.refresh_from_db()
        self.assertEqual(task.status, "TODO")
        self.assertTrue(task.metadata.get("ignore_execution_results"))
        self.assertEqual(task.metadata.get("stopped_run_token"), "run_1")
        self.assertIn("last_stop_request", task.metadata)

    @patch("tasks.execution.get_strategy")
    def test_stop_execution_failure_keeps_executing(self, mock_get_strategy):
        mock_strategy = mock_get_strategy.return_value
        mock_strategy.stop.return_value = {"ok": False, "engine": "local", "error": "cannot kill"}
        task = self.make_task(
            self.board,
            status="EXECUTING",
            assignee=self.user,
            metadata={"active_execution": {"run_token": "run_1", "pid": 1234}},
        )

        resp = self.client.post(f"/tasks/{task.id}/stop_execution/", {
            "updated_by": "alice@test.com",
            "target_status": "TODO",
        }, format="json")
        self.assertEqual(resp.status_code, 409)

        task.refresh_from_db()
        self.assertEqual(task.status, "EXECUTING")

    def test_update_status_blocked_while_executing(self):
        task = self.make_task(self.board, status="EXECUTING", assignee=self.user)
        resp = self.client.put(
            f"/tasks/{task.id}/",
            {"status": "REVIEW", "updated_by": "alice@test.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data.get("code"), "task_executing_locked")
        self.assertIn("status", resp.data.get("locked_fields", []))

    def test_update_model_blocked_while_executing(self):
        task = self.make_task(
            self.board,
            status="EXECUTING",
            assignee=self.user,
            model_name="gpt-4o-mini",
        )
        resp = self.client.put(
            f"/tasks/{task.id}/",
            {"model_name": "claude-sonnet", "updated_by": "alice@test.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data.get("code"), "task_executing_locked")
        self.assertIn("model", resp.data.get("locked_fields", []))

    def test_update_assignee_blocked_while_executing(self):
        new_user = self.make_user(email="newassignee@test.com")
        task = self.make_task(self.board, status="EXECUTING", assignee=self.user)
        resp = self.client.put(
            f"/tasks/{task.id}/",
            {"assignee_id": new_user.id, "updated_by": "alice@test.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data.get("code"), "task_executing_locked")
        self.assertIn("assignee", resp.data.get("locked_fields", []))

    def test_update_noop_locked_fields_allowed_while_executing(self):
        task = self.make_task(
            self.board,
            status="EXECUTING",
            assignee=self.user,
            model_name="gpt-4o-mini",
            metadata={"active_execution": {"run_token": "run_1"}},
        )
        resp = self.client.put(
            f"/tasks/{task.id}/",
            {
                "updated_by": "alice@test.com",
                "status": "EXECUTING",
                "assignee_id": self.user.id,
                "model_name": "gpt-4o-mini",
                "metadata": {"active_execution": {"run_token": "run_1"}, "started_at": 123.45},
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, "EXECUTING")
        self.assertEqual(task.assignee_id, self.user.id)
        self.assertEqual(task.model_name, "gpt-4o-mini")
        self.assertEqual((task.metadata or {}).get("started_at"), 123.45)

    def test_assign_action_blocked_while_executing(self):
        other_user = self.make_user(email="other@test.com")
        task = self.make_task(self.board, status="EXECUTING", assignee=self.user)
        resp = self.client.post(
            f"/tasks/{task.id}/assign/",
            {"assignee_id": other_user.id, "updated_by": "alice@test.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data.get("code"), "task_executing_locked")
        self.assertIn("assignee", resp.data.get("locked_fields", []))

    def test_unassign_action_blocked_while_executing(self):
        task = self.make_task(self.board, status="EXECUTING", assignee=self.user)
        resp = self.client.post(
            f"/tasks/{task.id}/unassign/",
            {"updated_by": "alice@test.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data.get("code"), "task_executing_locked")
        self.assertIn("assignee", resp.data.get("locked_fields", []))

    def test_execution_result_ignored_after_stop_flag(self):
        task = self.make_task(
            self.board,
            status="TODO",
            metadata={
                "ignore_execution_results": True,
                "stopped_run_token": "run_1",
            },
        )

        resp = self.client.post(f"/tasks/{task.id}/execution_result/", {
            "execution_result": {
                "success": True,
                "raw_output": "all good",
                "duration_ms": 100,
                "agent": "mock",
                "metadata": {"taskit_run_token": "run_1"},
            },
            "status": "REVIEW",
            "updated_by": "mock@odin.agent",
        }, format="json")
        self.assertEqual(resp.status_code, 200)

        task.refresh_from_db()
        self.assertEqual(task.status, "TODO")


# ═══════════════════════════════════════════════════════════════════════
# Assignment & board membership
# ═══════════════════════════════════════════════════════════════════════


class TestAssignmentAndMembership(APITestCase):
    """Tests the auto-board-membership behavior and assignment flows."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.alice = self.make_user(name="Alice", email="alice@test.com")
        self.bob = self.make_user(name="Bob", email="bob@test.com")

    def test_assign_auto_adds_to_board(self):
        """Assigning a user to a task auto-creates board membership."""
        task = self.make_task(self.board)
        self.assertFalse(BoardMembership.objects.filter(board=self.board, user=self.alice).exists())

        self.client.post(f"/tasks/{task.id}/assign/", {
            "assignee_id": self.alice.id,
            "updated_by": "lead@test.com",
        }, format="json")

        self.assertTrue(BoardMembership.objects.filter(board=self.board, user=self.alice).exists())

    def test_create_task_with_assignee_auto_adds_to_board(self):
        """Creating a task with an assignee auto-creates board membership."""
        self.client.post("/tasks/", {
            "board_id": self.board.id,
            "title": "Pre-assigned task",
            "created_by": "alice@test.com",
            "assignee_id": self.bob.id,
        }, format="json")

        self.assertTrue(BoardMembership.objects.filter(board=self.board, user=self.bob).exists())

    def test_reassign_task(self):
        task = self.make_task(self.board, assignee=self.alice)
        resp = self.client.post(f"/tasks/{task.id}/assign/", {
            "assignee_id": self.bob.id,
            "updated_by": "lead@test.com",
        }, format="json")
        self.assertEqual(resp.data["assignee"]["id"], self.bob.id)

        # History records the change
        history = TaskHistory.objects.filter(task=task, field_name="assignee_id").first()
        self.assertEqual(history.old_value, str(self.alice.id))
        self.assertEqual(history.new_value, str(self.bob.id))

    def test_unassign_task(self):
        task = self.make_task(self.board, assignee=self.alice)
        resp = self.client.post(f"/tasks/{task.id}/unassign/", {
            "updated_by": "lead@test.com",
        }, format="json")
        self.assertIsNone(resp.data["assignee"])

        history = TaskHistory.objects.filter(task=task, field_name="assignee_id").first()
        self.assertEqual(history.old_value, str(self.alice.id))
        self.assertEqual(history.new_value, "")

    def test_unassign_already_unassigned(self):
        """Unassigning a task with no assignee should not create history."""
        task = self.make_task(self.board)  # no assignee
        self.client.post(f"/tasks/{task.id}/unassign/", {
            "updated_by": "lead@test.com",
        }, format="json")
        history = TaskHistory.objects.filter(task=task, field_name="assignee_id")
        self.assertEqual(history.count(), 0)

    def test_bulk_add_members(self):
        self.client.post(f"/boards/{self.board.id}/members/add/", {
            "user_ids": [self.alice.id, self.bob.id],
        }, format="json")
        self.assertEqual(
            BoardMembership.objects.filter(board=self.board).count(), 2
        )

    def test_bulk_add_members_idempotent(self):
        """Adding the same members twice doesn't create duplicates."""
        for _ in range(2):
            self.client.post(f"/boards/{self.board.id}/members/add/", {
                "user_ids": [self.alice.id],
            }, format="json")
        self.assertEqual(
            BoardMembership.objects.filter(board=self.board, user=self.alice).count(), 1
        )

    def test_remove_member_unassigns_tasks(self):
        """Removing a member from a board unassigns their tasks and records history."""
        task = self.make_task(self.board, assignee=self.alice)
        BoardMembership.objects.create(board=self.board, user=self.alice)

        self.client.post(f"/boards/{self.board.id}/members/remove/", {
            "user_ids": [self.alice.id],
        }, format="json")

        task.refresh_from_db()
        self.assertIsNone(task.assignee)
        self.assertFalse(BoardMembership.objects.filter(board=self.board, user=self.alice).exists())

        # History records system unassignment
        history = TaskHistory.objects.filter(task=task, field_name="assignee_id").first()
        self.assertEqual(history.changed_by, "system@taskit")

    def test_list_board_members(self):
        BoardMembership.objects.create(board=self.board, user=self.alice)
        BoardMembership.objects.create(board=self.board, user=self.bob)

        resp = self.client.get(f"/boards/{self.board.id}/members/")
        self.assertEqual(len(resp.data), 2)
        emails = {u["email"] for u in resp.data}
        self.assertEqual(emails, {"alice@test.com", "bob@test.com"})


# ═══════════════════════════════════════════════════════════════════════
# Label management
# ═══════════════════════════════════════════════════════════════════════


class TestLabelManagement(APITestCase):

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.bug = self.make_label(name="bug", color="#ef4444")
        self.feature = self.make_label(name="feature", color="#22c55e")
        self.task = self.make_task(self.board)

    def test_add_labels(self):
        resp = self.client.post(f"/tasks/{self.task.id}/labels/", {
            "label_ids": [self.bug.id, self.feature.id],
            "updated_by": "alice@test.com",
        }, format="json")
        self.assertEqual(len(resp.data["labels"]), 2)

    def test_add_labels_records_history(self):
        self.client.post(f"/tasks/{self.task.id}/labels/", {
            "label_ids": [self.bug.id],
            "updated_by": "alice@test.com",
        }, format="json")

        history = TaskHistory.objects.filter(task=self.task, field_name="labels").first()
        self.assertIsNotNone(history)
        self.assertEqual(history.old_value, "[]")
        self.assertIn(str(self.bug.id), history.new_value)

    def test_remove_labels_via_update(self):
        """Remove labels by setting the label list via task update (the reliable path)."""
        self.task.labels.add(self.bug, self.feature)
        resp = self.client.put(f"/tasks/{self.task.id}/", {
            "label_ids": [self.feature.id],
            "updated_by": "alice@test.com",
        }, format="json")
        self.assertEqual(resp.status_code, 200)
        label_names = [l["name"] for l in resp.data["labels"]]
        self.assertEqual(label_names, ["feature"])

    def test_add_same_label_twice_no_duplicate_history(self):
        """Adding a label that already exists doesn't create a history entry."""
        self.task.labels.add(self.bug)
        self.client.post(f"/tasks/{self.task.id}/labels/", {
            "label_ids": [self.bug.id],
            "updated_by": "alice@test.com",
        }, format="json")
        history = TaskHistory.objects.filter(task=self.task, field_name="labels")
        self.assertEqual(history.count(), 0)

    def test_update_labels_via_task_update(self):
        """Labels can also be changed via the task update endpoint."""
        self.task.labels.add(self.bug)
        self.client.put(f"/tasks/{self.task.id}/", {
            "label_ids": [self.feature.id],
            "updated_by": "alice@test.com",
        }, format="json")

        self.task.refresh_from_db()
        label_ids = list(self.task.labels.values_list("id", flat=True))
        self.assertEqual(label_ids, [self.feature.id])


# ═══════════════════════════════════════════════════════════════════════
# Spec cloning (Odin re-run flow)
# ═══════════════════════════════════════════════════════════════════════


class TestSpecCloning(APITestCase):
    """Simulates Odin re-running a spec — clone creates a fresh copy."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.user = self.make_user()
        self.label = self.make_label()
        self.spec = self.make_spec(self.board, odin_id="sp_auth")

        # Create tasks under the spec with various states
        self.task1 = self.make_task(
            self.board, title="Design API", spec=self.spec,
            assignee=self.user, status="DONE", priority="HIGH",
        )
        self.task1.labels.add(self.label)
        self.task2 = self.make_task(
            self.board, title="Implement API", spec=self.spec,
            status="FAILED", complexity="HIGH",
            depends_on=["task_1"], metadata={"agent": "claude"},
        )

    def test_clone_creates_new_spec(self):
        resp = self.client.post(f"/specs/{self.spec.id}/clone/")
        self.assertEqual(resp.status_code, 201)
        self.assertIn("(CLONE)", resp.data["title"])
        self.assertNotEqual(resp.data["odin_id"], "sp_auth")
        self.assertFalse(resp.data["abandoned"])

    def test_clone_creates_new_tasks(self):
        resp = self.client.post(f"/specs/{self.spec.id}/clone/")
        new_tasks = resp.data["tasks"]
        self.assertEqual(len(new_tasks), 2)

        # All tasks reset to TODO
        for t in new_tasks:
            self.assertEqual(t["status"], "TODO")
            self.assertIn("(CLONE)", t["title"])

    def test_clone_preserves_task_properties(self):
        resp = self.client.post(f"/specs/{self.spec.id}/clone/")
        new_tasks = sorted(resp.data["tasks"], key=lambda t: t["id"])

        # First task keeps assignee, priority, labels
        self.assertEqual(new_tasks[0]["assignee"]["id"], self.user.id)
        self.assertEqual(new_tasks[0]["priority"], "HIGH")
        self.assertEqual(len(new_tasks[0]["labels"]), 1)

        # Second task keeps depends_on, complexity, metadata
        self.assertEqual(new_tasks[1]["depends_on"], ["task_1"])
        self.assertEqual(new_tasks[1]["complexity"], "HIGH")
        self.assertEqual(new_tasks[1]["metadata"], {"agent": "claude"})

    def test_clone_records_history(self):
        resp = self.client.post(f"/specs/{self.spec.id}/clone/")
        new_task_ids = [t["id"] for t in resp.data["tasks"]]

        for tid in new_task_ids:
            history = TaskHistory.objects.filter(task_id=tid)
            self.assertEqual(history.count(), 1)
            self.assertEqual(history.first().field_name, "created")
            self.assertIn("cloned from", history.first().new_value)

    def test_clone_does_not_modify_original(self):
        self.client.post(f"/specs/{self.spec.id}/clone/")

        # Original spec unchanged
        self.spec.refresh_from_db()
        self.assertEqual(self.spec.odin_id, "sp_auth")

        # Original tasks unchanged
        self.task1.refresh_from_db()
        self.assertEqual(self.task1.status, "DONE")
        self.assertEqual(self.task1.title, "Design API")

    def test_clone_is_atomic(self):
        """Total task count should increase exactly by the number of original tasks."""
        original_count = Task.objects.count()
        self.client.post(f"/specs/{self.spec.id}/clone/")
        self.assertEqual(Task.objects.count(), original_count + 2)


# ═══════════════════════════════════════════════════════════════════════
# Board clear operation
# ═══════════════════════════════════════════════════════════════════════


class TestBoardClear(APITestCase):

    def test_clear_removes_tasks_and_specs(self):
        board = self.make_board()
        spec = self.make_spec(board)
        self.make_task(board, spec=spec)
        self.make_task(board)

        resp = self.client.post(f"/boards/{board.id}/clear/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["tasks_deleted"], 2)
        self.assertEqual(resp.data["specs_deleted"], 1)
        self.assertEqual(Task.objects.count(), 0)

    def test_clear_does_not_affect_other_boards(self):
        board1 = self.make_board(name="B1")
        board2 = self.make_board(name="B2")
        self.make_task(board1, title="B1 task")
        self.make_task(board2, title="B2 task")

        self.client.post(f"/boards/{board1.id}/clear/")
        self.assertEqual(Task.objects.count(), 1)
        self.assertEqual(Task.objects.first().title, "B2 task")


# ═══════════════════════════════════════════════════════════════════════
# Odin integration flow: plan → create tasks → assign → execute → done
# ═══════════════════════════════════════════════════════════════════════


class TestOdinIntegrationFlow(APITestCase):
    """End-to-end simulation of how Odin uses the taskit API.

    Flow:
    1. Odin creates a board and spec via API
    2. Odin creates tasks with dependencies under the spec
    3. Tasks get assigned to agents
    4. Tasks progress through statuses
    5. Results are written back
    6. Dashboard reflects final state
    """

    def setUp(self):
        super().setUp()
        # Simulate Odin creating a user for itself
        resp = self.client.post("/users/", {
            "name": "Claude Agent", "email": "claude@odin.ai",
        }, format="json")
        self.agent = resp.data

    @patch("tasks.execution.get_strategy", return_value=None)
    def test_full_odin_flow(self, _mock):
        # 1. Create board
        resp = self.client.post("/boards/", {"name": "Odin Run #42"}, format="json")
        board_id = resp.data["id"]

        # 2. Create spec
        resp = self.client.post("/specs/", {
            "odin_id": "sp_20240101_001",
            "title": "Add user authentication",
            "board_id": board_id,
            "content": "Implement JWT-based auth with refresh tokens",
            "metadata": {"working_dir": "/tmp/project"},
        }, format="json")
        spec_id = resp.data["id"]

        # 3. Create tasks under spec with dependencies
        resp = self.client.post("/tasks/", {
            "board_id": board_id,
            "title": "Design auth schema",
            "created_by": "odin@system.com",
            "spec_id": spec_id,
            "priority": "HIGH",
            "complexity": "MEDIUM",
        }, format="json")
        task1_id = resp.data["id"]

        resp = self.client.post("/tasks/", {
            "board_id": board_id,
            "title": "Implement JWT middleware",
            "created_by": "odin@system.com",
            "spec_id": spec_id,
            "depends_on": [str(task1_id)],
            "complexity": "HIGH",
        }, format="json")
        task2_id = resp.data["id"]

        resp = self.client.post("/tasks/", {
            "board_id": board_id,
            "title": "Write auth tests",
            "created_by": "odin@system.com",
            "spec_id": spec_id,
            "depends_on": [str(task1_id), str(task2_id)],
        }, format="json")
        task3_id = resp.data["id"]

        # 4. Assign all tasks to agent
        for tid in (task1_id, task2_id, task3_id):
            self.client.post(f"/tasks/{tid}/assign/", {
                "assignee_id": self.agent["id"],
                "updated_by": "odin@system.com",
            }, format="json")

        # 5. Execute task 1: design → in_progress → done
        self.client.put(f"/tasks/{task1_id}/", {
            "status": "IN_PROGRESS",
            "updated_by": "odin@system.com",
        }, format="json")
        self.client.put(f"/tasks/{task1_id}/", {
            "status": "DONE",
            "result": "Schema designed: users table with JWT claims",
            "updated_by": "odin@system.com",
        }, format="json")

        # 6. Execute task 2: implementation
        self.client.put(f"/tasks/{task2_id}/", {
            "status": "IN_PROGRESS",
            "updated_by": "odin@system.com",
        }, format="json")
        self.client.put(f"/tasks/{task2_id}/", {
            "status": "DONE",
            "result": "JWT middleware implemented in auth/middleware.py",
            "updated_by": "odin@system.com",
        }, format="json")

        # 7. Execute task 3: tests (this one fails)
        self.client.put(f"/tasks/{task3_id}/", {
            "status": "IN_PROGRESS",
            "updated_by": "odin@system.com",
        }, format="json")
        self.client.put(f"/tasks/{task3_id}/", {
            "status": "FAILED",
            "result": "3 of 10 tests failing: test_refresh_token, test_expiry, test_revocation",
            "updated_by": "odin@system.com",
        }, format="json")

        # 8. Verify task/spec list endpoints show correct final state
        resp = self.client.get(f"/tasks/?board_id={board_id}")
        tasks = resp.data["results"]
        task_map = {t["id"]: t for t in tasks}

        self.assertEqual(task_map[task1_id]["status"], "DONE")
        self.assertEqual(task_map[task2_id]["status"], "DONE")
        self.assertEqual(task_map[task3_id]["status"], "FAILED")

        # Spec shows all 3 tasks via task_count
        specs = self.client.get(f"/specs/?board_id={board_id}").data["results"]
        spec_data = next(s for s in specs if s["id"] == spec_id)
        self.assertEqual(spec_data["task_count"], 3)

        # Agent is a board member
        self.assertTrue(
            BoardMembership.objects.filter(
                board_id=board_id, user_id=self.agent["id"]
            ).exists()
        )


# ═══════════════════════════════════════════════════════════════════════
# JSON field tracking (depends_on, metadata)
# ═══════════════════════════════════════════════════════════════════════


class TestJSONFieldTracking(APITestCase):

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def test_depends_on_change_tracked(self):
        task = self.make_task(self.board, depends_on=["task_1"])
        self.client.put(f"/tasks/{task.id}/", {
            "depends_on": ["task_1", "task_2"],
            "updated_by": "alice@test.com",
        }, format="json")

        history = TaskHistory.objects.filter(task=task, field_name="depends_on").first()
        self.assertIsNotNone(history)

    def test_metadata_change_tracked(self):
        task = self.make_task(self.board, metadata={"v": 1})
        self.client.put(f"/tasks/{task.id}/", {
            "metadata": {"v": 2, "agent": "gemini"},
            "updated_by": "alice@test.com",
        }, format="json")

        history = TaskHistory.objects.filter(task=task, field_name="metadata").first()
        self.assertIsNotNone(history)

    def test_same_metadata_no_history(self):
        task = self.make_task(self.board, metadata={"k": "v"})
        self.client.put(f"/tasks/{task.id}/", {
            "metadata": {"k": "v"},
            "updated_by": "alice@test.com",
        }, format="json")

        history = TaskHistory.objects.filter(task=task, field_name="metadata")
        self.assertEqual(history.count(), 0)


# ═══════════════════════════════════════════════════════════════════════
# Assignee update via task update endpoint
# ═══════════════════════════════════════════════════════════════════════


class TestAssigneeViaUpdate(APITestCase):

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.alice = self.make_user(name="Alice", email="alice@test.com")
        self.bob = self.make_user(name="Bob", email="bob@test.com")

    def test_assign_via_update(self):
        task = self.make_task(self.board)
        resp = self.client.put(f"/tasks/{task.id}/", {
            "assignee_id": self.alice.id,
            "updated_by": "lead@test.com",
        }, format="json")
        self.assertEqual(resp.data["assignee"]["id"], self.alice.id)

    def test_reassign_via_update(self):
        task = self.make_task(self.board, assignee=self.alice)
        resp = self.client.put(f"/tasks/{task.id}/", {
            "assignee_id": self.bob.id,
            "updated_by": "lead@test.com",
        }, format="json")
        self.assertEqual(resp.data["assignee"]["id"], self.bob.id)

        history = TaskHistory.objects.filter(task=task, field_name="assignee_id").first()
        self.assertEqual(history.old_value, str(self.alice.id))
        self.assertEqual(history.new_value, str(self.bob.id))

    def test_unassign_via_update(self):
        task = self.make_task(self.board, assignee=self.alice)
        resp = self.client.put(f"/tasks/{task.id}/", {
            "assignee_id": None,
            "updated_by": "lead@test.com",
        }, format="json")
        self.assertIsNone(resp.data["assignee"])

    def test_assign_via_update_auto_membership(self):
        """Assigning via PUT also auto-creates board membership."""
        task = self.make_task(self.board)
        self.client.put(f"/tasks/{task.id}/", {
            "assignee_id": self.bob.id,
            "updated_by": "lead@test.com",
        }, format="json")
        self.assertTrue(BoardMembership.objects.filter(board=self.board, user=self.bob).exists())


# ═══════════════════════════════════════════════════════════════════════
# Task filtering by spec
# ═══════════════════════════════════════════════════════════════════════


class TestTaskSpecFiltering(APITestCase):

    def test_filter_tasks_by_spec(self):
        board = self.make_board()
        spec1 = self.make_spec(board, odin_id="sp_001")
        spec2 = self.make_spec(board, odin_id="sp_002", title="Spec 2")
        self.make_task(board, title="Spec1 task", spec=spec1)
        self.make_task(board, title="Spec2 task", spec=spec2)
        self.make_task(board, title="No spec task")

        resp = self.client.get(f"/tasks/?spec_id={spec1.id}")
        results = self.results(resp)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Spec1 task")
