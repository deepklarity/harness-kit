"""CRUD tests for all taskit REST endpoints.

Covers: User, Board, Label, Task, Spec — create, read, update, delete.
Each test method is self-contained; the base class provides factory helpers.
"""

import tempfile
from pathlib import Path

from .base import APITestCase


# ═══════════════════════════════════════════════════════════════════════
# User CRUD
# ═══════════════════════════════════════════════════════════════════════


class TestUserCRUD(APITestCase):

    def test_create_user(self):
        resp = self.client.post("/users/", {"name": "Bob", "email": "bob@test.com"}, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["name"], "Bob")
        self.assertEqual(resp.data["email"], "bob@test.com")
        self.assertIn("id", resp.data)

    def test_list_users(self):
        self.make_user(name="Alice", email="alice@test.com")
        self.make_user(name="Bob", email="bob@test.com")
        resp = self.client.get("/users/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(self.results(resp)), 2)

    def test_get_user(self):
        user = self.make_user()
        resp = self.client.get(f"/users/{user.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["email"], "alice@test.com")

    def test_update_user(self):
        user = self.make_user()
        resp = self.client.put(f"/users/{user.id}/", {"name": "Alice Updated"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["name"], "Alice Updated")
        # Email unchanged (partial update)
        self.assertEqual(resp.data["email"], "alice@test.com")

    def test_delete_user(self):
        user = self.make_user()
        resp = self.client.delete(f"/users/{user.id}/")
        self.assertEqual(resp.status_code, 204)
        resp = self.client.get(f"/users/{user.id}/")
        self.assertEqual(resp.status_code, 404)

    def test_create_user_duplicate_email(self):
        self.make_user(email="alice@test.com")
        resp = self.client.post("/users/", {"name": "Alice2", "email": "alice@test.com"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_user_default_color(self):
        resp = self.client.post("/users/", {"name": "Bob", "email": "bob@test.com"}, format="json")
        self.assertEqual(resp.data["color"], "#6366f1")

    def test_get_nonexistent_user(self):
        resp = self.client.get("/users/999/")
        self.assertEqual(resp.status_code, 404)


# ═══════════════════════════════════════════════════════════════════════
# Board CRUD
# ═══════════════════════════════════════════════════════════════════════


class TestBoardCRUD(APITestCase):

    def test_create_board(self):
        resp = self.client.post("/boards/", {"name": "Sprint 1"}, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["name"], "Sprint 1")

    def test_list_boards(self):
        self.make_board(name="Sprint 1")
        self.make_board(name="Sprint 2")
        resp = self.client.get("/boards/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(self.results(resp)), 2)

    def test_get_board_detail(self):
        board = self.make_board()
        task = self.make_task(board, title="Task 1")
        resp = self.client.get(f"/boards/{board.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["name"], "Sprint 1")
        # BoardDetailSerializer includes nested tasks
        self.assertEqual(len(resp.data["tasks"]), 1)
        self.assertEqual(resp.data["tasks"][0]["title"], "Task 1")

    def test_update_board(self):
        board = self.make_board()
        resp = self.client.put(f"/boards/{board.id}/", {"name": "Sprint 1 Updated"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["name"], "Sprint 1 Updated")

    def test_delete_board_cascades_tasks(self):
        board = self.make_board()
        self.make_task(board)
        resp = self.client.delete(f"/boards/{board.id}/")
        self.assertEqual(resp.status_code, 204)
        from tasks.models import Task
        self.assertEqual(Task.objects.count(), 0)

    def test_board_member_ids_empty(self):
        board = self.make_board()
        resp = self.client.get(f"/boards/{board.id}/")
        self.assertEqual(resp.data["member_ids"], [])

    def test_board_is_trial_default(self):
        resp = self.client.post("/boards/", {"name": "Trial"}, format="json")
        self.assertFalse(resp.data["is_trial"])


# ═══════════════════════════════════════════════════════════════════════
# Label CRUD
# ═══════════════════════════════════════════════════════════════════════


class TestLabelCRUD(APITestCase):

    def test_create_label(self):
        resp = self.client.post("/labels/", {"name": "bug", "color": "#ef4444"}, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["name"], "bug")

    def test_list_labels(self):
        self.make_label(name="bug")
        self.make_label(name="feature", color="#22c55e")
        resp = self.client.get("/labels/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(self.results(resp)), 2)

    def test_update_label(self):
        label = self.make_label()
        resp = self.client.put(f"/labels/{label.id}/", {"name": "critical-bug"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["name"], "critical-bug")

    def test_delete_label(self):
        label = self.make_label()
        resp = self.client.delete(f"/labels/{label.id}/")
        self.assertEqual(resp.status_code, 204)


# ═══════════════════════════════════════════════════════════════════════
# Task CRUD
# ═══════════════════════════════════════════════════════════════════════


class TestTaskCRUD(APITestCase):

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.user = self.make_user()

    def test_create_task_with_email(self):
        resp = self.make_task_via_api(self.board, title="My Task", created_by="alice@test.com")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["title"], "My Task")
        self.assertEqual(resp.data["status"], "TODO")
        self.assertEqual(resp.data["priority"], "MEDIUM")

    def test_create_task_with_user_id(self):
        resp = self.client.post("/tasks/", {
            "board_id": self.board.id,
            "title": "By User ID",
            "created_by_user_id": self.user.id,
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["created_by"], self.user.email)

    def test_create_task_requires_created_by(self):
        resp = self.client.post("/tasks/", {
            "board_id": self.board.id,
            "title": "No author",
        }, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_create_task_with_assignee(self):
        resp = self.client.post("/tasks/", {
            "board_id": self.board.id,
            "title": "Assigned task",
            "created_by": "alice@test.com",
            "assignee_id": self.user.id,
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["assignee"]["id"], self.user.id)

    def test_create_task_with_labels(self):
        label = self.make_label()
        resp = self.client.post("/tasks/", {
            "board_id": self.board.id,
            "title": "Labeled task",
            "created_by": "alice@test.com",
            "label_ids": [label.id],
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(len(resp.data["labels"]), 1)

    def test_create_task_records_history(self):
        resp = self.make_task_via_api(self.board)
        task_id = resp.data["id"]
        from tasks.models import TaskHistory
        history = TaskHistory.objects.filter(task_id=task_id)
        self.assertEqual(history.count(), 1)
        self.assertEqual(history.first().field_name, "created")

    def test_list_tasks(self):
        self.make_task(self.board, title="Task 1")
        self.make_task(self.board, title="Task 2")
        resp = self.client.get("/tasks/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(self.results(resp)), 2)

    def test_list_tasks_filter_by_board(self):
        board2 = self.make_board(name="Sprint 2")
        self.make_task(self.board, title="Board 1 task")
        self.make_task(board2, title="Board 2 task")
        resp = self.client.get(f"/tasks/?board_id={self.board.id}")
        results = self.results(resp)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Board 1 task")

    def test_get_task(self):
        task = self.make_task(self.board, title="Detail task")
        resp = self.client.get(f"/tasks/{task.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["title"], "Detail task")

    def test_update_task(self):
        task = self.make_task(self.board)
        resp = self.client.put(f"/tasks/{task.id}/", {
            "title": "Updated title",
            "updated_by": "alice@test.com",
        }, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["title"], "Updated title")

    def test_update_task_requires_updated_by(self):
        task = self.make_task(self.board)
        resp = self.client.put(f"/tasks/{task.id}/", {
            "title": "No updater",
        }, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_delete_task(self):
        task = self.make_task(self.board)
        resp = self.client.delete(f"/tasks/{task.id}/")
        self.assertEqual(resp.status_code, 204)

    def test_create_task_with_odin_fields(self):
        spec = self.make_spec(self.board)
        resp = self.client.post("/tasks/", {
            "board_id": self.board.id,
            "title": "Odin task",
            "created_by": "alice@test.com",
            "spec_id": spec.id,
            "depends_on": ["task_1", "task_2"],
            "complexity": "HIGH",
            "metadata": {"agent": "claude"},
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["spec_id"], spec.id)
        self.assertEqual(resp.data["depends_on"], ["task_1", "task_2"])
        self.assertEqual(resp.data["complexity"], "HIGH")
        self.assertEqual(resp.data["metadata"], {"agent": "claude"})

    def test_create_task_with_priority_and_status(self):
        resp = self.client.post("/tasks/", {
            "board_id": self.board.id,
            "title": "Critical task",
            "created_by": "alice@test.com",
            "priority": "CRITICAL",
            "status": "BACKLOG",
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["priority"], "CRITICAL")
        self.assertEqual(resp.data["status"], "BACKLOG")


    def test_create_task_invalid_priority(self):
        resp = self.client.post("/tasks/", {
            "board_id": self.board.id,
            "title": "Bad priority",
            "created_by": "alice@test.com",
            "priority": "ULTRA",
        }, format="json")
        self.assertEqual(resp.status_code, 400)


class TestRuntimeDirectoryEndpoints(APITestCase):
    def test_runtime_directory_suggest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "alpha-project").mkdir()
            (base / "alpha-tools").mkdir()
            (base / "beta").mkdir()

            query = f"{base}/al"
            resp = self.client.get(f"/api/runtime/directories/suggest/?q={query}")
            self.assertEqual(resp.status_code, 200)
            paths = [entry["path"] for entry in resp.data["entries"]]
            self.assertIn(str((base / "alpha-project").resolve()), paths)
            self.assertIn(str((base / "alpha-tools").resolve()), paths)
            self.assertNotIn(str((base / "beta").resolve()), paths)

    def test_runtime_directory_children(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "frontend").mkdir()
            (base / "backend").mkdir()
            (base / "README.md").write_text("x", encoding="utf-8")

            resp = self.client.get(f"/api/runtime/directories/children/?path={base}")
            self.assertEqual(resp.status_code, 200)
            names = [entry["name"] for entry in resp.data["entries"]]
            self.assertIn("frontend", names)
            self.assertIn("backend", names)
            self.assertNotIn("README.md", names)

    def test_runtime_directory_children_rejects_relative_path(self):
        resp = self.client.get("/api/runtime/directories/children/?path=relative/path")
        self.assertEqual(resp.status_code, 400)


# ═══════════════════════════════════════════════════════════════════════
# Spec CRUD
# ═══════════════════════════════════════════════════════════════════════


class TestSpecCRUD(APITestCase):

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def test_create_spec(self):
        resp = self.client.post("/specs/", {
            "odin_id": "sp_001",
            "title": "Auth Feature",
            "board_id": self.board.id,
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["odin_id"], "sp_001")
        self.assertEqual(resp.data["title"], "Auth Feature")
        self.assertEqual(resp.data["source"], "inline")

    def test_list_specs(self):
        self.make_spec(self.board, odin_id="sp_001")
        self.make_spec(self.board, odin_id="sp_002", title="Spec 2")
        resp = self.client.get("/specs/")
        self.assertEqual(len(self.results(resp)), 2)

    def test_list_specs_filter_by_board(self):
        board2 = self.make_board(name="Sprint 2")
        self.make_spec(self.board, odin_id="sp_001")
        self.make_spec(board2, odin_id="sp_002")
        resp = self.client.get(f"/specs/?board_id={self.board.id}")
        self.assertEqual(len(self.results(resp)), 1)

    def test_list_specs_filter_by_odin_id(self):
        self.make_spec(self.board, odin_id="sp_001")
        self.make_spec(self.board, odin_id="sp_002", title="Spec 2")
        resp = self.client.get("/specs/?odin_id=sp_001")
        results = self.results(resp)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["odin_id"], "sp_001")

    def test_get_spec_with_tasks(self):
        spec = self.make_spec(self.board)
        self.make_task(self.board, title="Spec task", spec=spec)
        resp = self.client.get(f"/specs/{spec.id}/")
        self.assertEqual(len(resp.data["tasks"]), 1)

    def test_update_spec(self):
        spec = self.make_spec(self.board)
        resp = self.client.put(f"/specs/{spec.id}/", {
            "title": "Updated Spec",
            "abandoned": True,
        }, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["title"], "Updated Spec")
        self.assertTrue(resp.data["abandoned"])

    def test_delete_spec_cascades_tasks(self):
        spec = self.make_spec(self.board)
        self.make_task(self.board, title="Spec task", spec=spec)
        resp = self.client.delete(f"/specs/{spec.id}/")
        self.assertEqual(resp.status_code, 204)
        from tasks.models import Task
        self.assertEqual(Task.objects.count(), 0)

    def test_create_spec_duplicate_odin_id(self):
        self.make_spec(self.board, odin_id="sp_001")
        resp = self.client.post("/specs/", {
            "odin_id": "sp_001",
            "title": "Duplicate",
            "board_id": self.board.id,
        }, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_spec_diagnostic(self):
        spec = self.make_spec(self.board)
        task = self.make_task(self.board, title="Diag task", spec=spec)
        # Add history and comment
        from tasks.models import TaskComment, TaskHistory
        TaskHistory.objects.create(
            task=task, field_name="created", old_value="",
            new_value="Task created", changed_by="alice@test.com",
        )
        TaskComment.objects.create(
            task=task, author_email="alice@test.com",
            content="Test comment",
        )
        resp = self.client.get(f"/specs/{spec.id}/diagnostic/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["odin_id"], "sp_001")
        self.assertEqual(resp.data["board_name"], "Sprint 1")
        self.assertEqual(len(resp.data["tasks"]), 1)
        task_data = resp.data["tasks"][0]
        self.assertIn("history", task_data)
        self.assertIn("comments", task_data)
        self.assertEqual(len(task_data["comments"]), 1)
        self.assertEqual(task_data["comments"][0]["content"], "Test comment")

    def test_spec_diagnostic_not_found(self):
        resp = self.client.get("/specs/999/diagnostic/")
        self.assertEqual(resp.status_code, 404)

    def test_create_spec_with_metadata(self):
        resp = self.client.post("/specs/", {
            "odin_id": "sp_meta",
            "title": "With Metadata",
            "board_id": self.board.id,
            "metadata": {"working_dir": "/tmp/project"},
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["metadata"]["working_dir"], "/tmp/project")


# ═══════════════════════════════════════════════════════════════════════
# API aliases
# ═══════════════════════════════════════════════════════════════════════


class TestApiAliases(APITestCase):

    def test_api_tasks_alias(self):
        board = self.make_board()
        self.make_task(board, title="Task via root")
        resp = self.client.get("/api/tasks/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(self.results(resp)), 1)

    def test_api_members_alias(self):
        self.make_user(name="Alice", email="alice@test.com")
        resp = self.client.get("/api/members/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(self.results(resp)), 1)



# ═══════════════════════════════════════════════════════════════════════
# Health check
# ═══════════════════════════════════════════════════════════════════════


class TestHealthCheck(APITestCase):

    def test_health(self):
        resp = self.client.get("/health/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")
