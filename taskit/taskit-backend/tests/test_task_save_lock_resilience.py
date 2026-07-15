"""Task-save lock resilience (task #364).

The task-save path (PATCH /tasks/<id>/) and its sibling write endpoints
agents/operators hit routinely must survive a transient SQLite
``database is locked`` — the same bounded backstop move_task and the
TaskRun heartbeat already have (``tasks.db.retry_on_locked``). Without it,
three rapid dispatches collided and surfaced 500s (OperationalError) that
only succeeded on a manual retry seconds later.

These tests assert the writes retry and succeed rather than 500.
"""

import json
import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import patch

from django.db import OperationalError

from tests.base import APITestCase
from tasks import views as views_module
from tasks.models import SystemSetting, Task, TaskComment, TaskHistory, TaskStatus


def _flaky_save_once(real_save):
    """Wrap a real Model.save to raise 'database is locked' on the first
    call then delegate — mirrors the move_task flaky-persist pattern
    (tests/test_kanban_ordering.py)."""
    raised = {"v": False}

    def save(self, *args, **kwargs):
        if not raised["v"]:
            raised["v"] = True
            raise OperationalError("database is locked")
        return real_save(self, *args, **kwargs)

    return save, raised


@patch("tasks.db.time.sleep")
class TaskSaveLockResilienceTests(APITestCase):
    """PATCH /tasks/<id>/ retries a transient 'database is locked' on the
    task.save() + history bulk_create instead of surfacing a 500."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board, title="original", status=TaskStatus.TODO)

    def test_patch_task_retries_lock_then_succeeds(self, _sleep):
        # The acceptance criterion: a transient lock on the save is retried,
        # the PATCH succeeds, and the field change + history both land.
        flaky, raised = _flaky_save_once(Task.save)

        with patch.object(Task, "save", flaky):
            resp = self.client.patch(
                f"/tasks/{self.task.id}/",
                {"title": "renamed", "updated_by": "op@test.com"},
                format="json",
            )

        self.assertTrue(raised["v"], "the lock path was never exercised")
        self.assertEqual(resp.status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, "renamed")
        self.assertTrue(
            TaskHistory.objects.filter(
                task=self.task, field_name="title", new_value="renamed"
            ).exists()
        )

    def test_patch_task_non_lock_error_surfaces_immediately(self, _sleep):
        # A non-lock OperationalError (schema problem) must NOT be retried —
        # retrying masks a real bug. It surfaces on the first call.
        calls = {"n": 0}

        def hard_fail(self, *args, **kwargs):
            calls["n"] += 1
            raise OperationalError("no such column: tasks_task.bogus")

        with patch.object(Task, "save", hard_fail):
            with self.assertRaises(OperationalError):
                self.client.patch(
                    f"/tasks/{self.task.id}/",
                    {"title": "renamed", "updated_by": "op@test.com"},
                    format="json",
                )
        self.assertEqual(calls["n"], 1)


class PersistTaskUpdateUnitTests(APITestCase):
    """_persist_task_update — atomic save + bulk_create retried on lock."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    @patch("tasks.db.time.sleep")
    def test_retries_save_lock_then_succeeds(self, _sleep):
        task = self.make_task(self.board, title="t", status=TaskStatus.TODO)
        history = TaskHistory(
            task=task, field_name="title", old_value="t", new_value="t2", changed_by="x",
        )
        flaky, raised = _flaky_save_once(Task.save)

        with patch.object(Task, "save", flaky):
            views_module._persist_task_update(task, [history])

        self.assertTrue(raised["v"])
        self.assertTrue(
            TaskHistory.objects.filter(task=task, field_name="title").exists()
        )

    @patch("tasks.db.time.sleep")
    def test_atomic_rollback_then_retry_on_bulk_create_lock(self, _sleep):
        # Lock on bulk_create (the 2nd write). The atomic block rolls back the
        # save so the retry is clean — exactly one history row, no phantoms.
        task = self.make_task(self.board, title="t", status=TaskStatus.TODO)
        history = TaskHistory(
            task=task, field_name="title", old_value="t", new_value="t2", changed_by="x",
        )
        real_bulk = TaskHistory.objects.bulk_create
        raised = {"v": False}

        def flaky_bulk(*args, **kwargs):
            if not raised["v"]:
                raised["v"] = True
                raise OperationalError("database is locked")
            return real_bulk(*args, **kwargs)

        with patch.object(TaskHistory.objects, "bulk_create", flaky_bulk):
            views_module._persist_task_update(task, [history])

        self.assertTrue(raised["v"])
        self.assertEqual(
            TaskHistory.objects.filter(task=task, field_name="title").count(), 1
        )


@patch("tasks.db.time.sleep")
class CommentPostLockResilienceTests(APITestCase):
    """POST /tasks/<id>/comments/ retries a transient lock on comment create."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board, title="t", status=TaskStatus.TODO)

    def test_comment_post_retries_lock_then_succeeds(self, _sleep):
        real_create = TaskComment.objects.create
        raised = {"v": False}

        def flaky_create(*args, **kwargs):
            if not raised["v"]:
                raised["v"] = True
                raise OperationalError("database is locked")
            return real_create(*args, **kwargs)

        with patch.object(TaskComment.objects, "create", flaky_create):
            resp = self.client.post(
                f"/tasks/{self.task.id}/comments/",
                {"author_email": "a@b.com", "content": "hello"},
                format="json",
            )

        self.assertTrue(raised["v"])
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(TaskComment.objects.filter(task=self.task, content="hello").exists())


@patch("tasks.db.time.sleep")
class ExecutionResultLockResilienceTests(APITestCase):
    """POST /tasks/<id>/execution_result/ retries a transient lock on the
    task save + history write (the heaviest agent write path)."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def test_execution_result_retries_save_lock_then_succeeds(self, _sleep):
        executing = self.make_task(
            self.board, title="exec", status="EXECUTING", kanban_position=0,
        )
        flaky, raised = _flaky_save_once(Task.save)

        with patch("tasks.views._trigger_auto_reflection", lambda task: None), \
                patch.object(Task, "save", flaky):
            resp = self.client.post(
                f"/tasks/{executing.id}/execution_result/",
                {
                    "execution_result": {
                        "success": True,
                        "raw_output": "done",
                        "duration_ms": 1200.0,
                        "agent": "mock",
                        "metadata": {},
                    },
                    "status": "REVIEW",
                    "updated_by": "mock@odin.agent",
                },
                format="json",
            )

        self.assertTrue(raised["v"])
        self.assertEqual(resp.status_code, 200)
        executing.refresh_from_db()
        self.assertEqual(executing.status, "REVIEW")


@patch("tasks.db.time.sleep")
class SaveWithRetryUnitTests(APITestCase):
    """_save_with_retry — the generic instance-save backstop used by the
    settings endpoint (operator-facing single-row writes)."""

    def test_retries_lock_then_succeeds(self, _sleep):
        board = self.make_board()
        task = self.make_task(board, title="t")
        flaky, raised = _flaky_save_once(Task.save)

        task.title = "renamed"
        with patch.object(Task, "save", flaky):
            views_module._save_with_retry(task, update_fields=["title"])

        self.assertTrue(raised["v"])
        task.refresh_from_db()
        self.assertEqual(task.title, "renamed")


@patch("tasks.db.time.sleep")
class AssignLockResilienceTests(APITestCase):
    """POST /tasks/<id>/assign/ retries a transient lock on the task save +
    history write. Operators hit this routinely when dispatching."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board, title="t", status=TaskStatus.TODO)
        self.assignee = self.make_user(name="Bob", email="bob@test.com")

    def test_assign_retries_save_lock_then_succeeds(self, _sleep):
        flaky, raised = _flaky_save_once(Task.save)

        with patch("tasks.notification_service.notify", lambda **kwargs: None), \
                patch("tasks.views._ensure_board_membership", lambda *a, **kw: None), \
                patch.object(Task, "save", flaky):
            resp = self.client.post(
                f"/tasks/{self.task.id}/assign/",
                {"assignee_id": self.assignee.id, "updated_by": "op@test.com"},
                format="json",
            )

        self.assertTrue(raised["v"], "the lock path was never exercised")
        self.assertEqual(resp.status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.assignee_id, self.assignee.id)
        self.assertTrue(
            TaskHistory.objects.filter(
                task=self.task, field_name="assignee_id", new_value=str(self.assignee.id),
            ).exists()
        )


@patch("tasks.db.time.sleep")
class UnassignLockResilienceTests(APITestCase):
    """POST /tasks/<id>/unassign/ retries a transient lock on the task save +
    history write."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.assignee = self.make_user(name="Bob", email="bob@test.com")
        self.task = self.make_task(
            self.board, title="t", status=TaskStatus.TODO, assignee=self.assignee,
        )

    def test_unassign_retries_save_lock_then_succeeds(self, _sleep):
        flaky, raised = _flaky_save_once(Task.save)

        with patch.object(Task, "save", flaky):
            resp = self.client.post(
                f"/tasks/{self.task.id}/unassign/",
                {"updated_by": "op@test.com"},
                format="json",
            )

        self.assertTrue(raised["v"], "the lock path was never exercised")
        self.assertEqual(resp.status_code, 200)
        self.task.refresh_from_db()
        self.assertIsNone(self.task.assignee_id)
        self.assertTrue(
            TaskHistory.objects.filter(
                task=self.task, field_name="assignee_id", new_value="",
            ).exists()
        )


@patch("tasks.db.time.sleep")
class LabelChangeLockResilienceTests(APITestCase):
    """POST/DELETE /tasks/<id>/labels/ retries a transient lock on the
    M2M join write + history row."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board, title="t", status=TaskStatus.TODO)
        self.label = self.make_label(name="bug", color="#ef4444")

    def test_add_labels_retries_lock_then_succeeds(self, _sleep):
        # Patch TaskHistory.objects.create to raise once — that fires inside
        # the _persist_label_change atomic block; the whole endpoint should
        # retry the atomic block (M2M add + history create) and succeed.
        real_create = TaskHistory.objects.create
        raised = {"v": False}

        def flaky_create(*args, **kwargs):
            if not raised["v"]:
                raised["v"] = True
                raise OperationalError("database is locked")
            return real_create(*args, **kwargs)

        with patch.object(TaskHistory.objects, "create", flaky_create):
            resp = self.client.post(
                f"/tasks/{self.task.id}/labels/",
                {"label_ids": [self.label.id], "updated_by": "op@test.com"},
                format="json",
            )

        self.assertTrue(raised["v"], "the lock path was never exercised")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self.task.labels.filter(id=self.label.id).exists())
        self.assertTrue(
            TaskHistory.objects.filter(task=self.task, field_name="labels").exists()
        )

    def test_remove_labels_via_viewset_call_retries_lock_then_succeeds(self, _sleep):
        # Note: the DELETE /tasks/<id>/labels/ route currently routes to
        # add_labels (two @action decorators with the same url_path share a
        # router slot, and DRF registers only the last one). This is a
        # pre-existing routing bug, not in scope here. We still want to
        # confirm the underlying helper is lock-resilient, so we exercise
        # the viewset method directly.
        self.task.labels.add(self.label)
        real_create = TaskHistory.objects.create
        raised = {"v": False}

        def flaky_create(*args, **kwargs):
            if not raised["v"]:
                raised["v"] = True
                raise OperationalError("database is locked")
            return real_create(*args, **kwargs)

        with patch.object(TaskHistory.objects, "create", flaky_create):
            views_module._persist_label_change(
                self.task,
                m2m_change=lambda t: (
                    t.labels.remove(self.label),
                    TaskHistory.objects.create(
                        task=t, schedule_run=t.current_schedule_run,
                        field_name="labels",
                        old_value=json.dumps([self.label.id]),
                        new_value="[]",
                        changed_by="op@test.com",
                    ),
                ),
            )

        self.assertTrue(raised["v"], "the lock path was never exercised")
        self.assertFalse(self.task.labels.filter(id=self.label.id).exists())


@patch("tasks.db.time.sleep")
class ExecutorSettingsLockResilienceTests(APITestCase):
    def test_max_concurrency_post_retries_lock_then_succeeds(self, _sleep):
        real_update_or_create = SystemSetting.objects.update_or_create
        calls = {"n": 0}

        def flaky_update_or_create(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OperationalError("database is locked")
            return real_update_or_create(*args, **kwargs)

        with patch.object(
            SystemSetting.objects, "update_or_create", flaky_update_or_create
        ):
            response = self.client.post(
                "/executor/max-concurrency/", {"value": 5}, format="json"
            )

        self.assertEqual(calls["n"], 2)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            SystemSetting.objects.get(key="executor_max_concurrency").value, 5
        )


@patch("tasks.db.time.sleep")
class RapidDispatchLoopTests(APITestCase):
    """The five-shot PATCH loop the brief asks for: rapid-dispatch
    operator commands fire PATCHes back-to-back, the bounded retry
    backstop absorbs each transient lock, every PATCH lands its data and
    writes exactly one history row (task #364)."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board, title="original", status=TaskStatus.TODO)

    def test_rapid_dispatch_loop_all_succeed(self, _sleep):
        """Five PATCHes fired back-to-back, each one encountering a
        transient lock on the first save attempt — every PATCH still
        returns 200, the final state reflects the last PATCH, and exactly
        one history row lands per PATCH (no phantom duplicates from the
        retry)."""
        responses = []
        for i in range(5):
            flaky, raised = _flaky_save_once(Task.save)
            with patch.object(Task, "save", flaky):
                resp = self.client.patch(
                    f"/tasks/{self.task.id}/",
                    {"title": f"rename-{i}", "updated_by": "op@test.com"},
                    format="json",
                )
            self.assertTrue(raised["v"], f"PATCH {i} never hit the lock path")
            responses.append(resp.status_code)

        self.assertEqual(responses, [200, 200, 200, 200, 200])
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, "rename-4")
        self.assertEqual(
            TaskHistory.objects.filter(task=self.task, field_name="title").count(),
            5,
            "expected exactly one history row per PATCH (no phantoms)",
        )
