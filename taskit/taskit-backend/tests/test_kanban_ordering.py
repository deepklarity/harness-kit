from datetime import timedelta
from unittest.mock import patch

from django.db import OperationalError
from django.utils import timezone

from .base import APITestCase

from tasks.models import TaskComment, TaskHistory


class TestKanbanOrdering(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.user = self.make_user()

    def _land(self, task, status, when):
        """Record a status-transition history row with an explicit
        timestamp (bypassing auto_now_add) so tests can control recency
        order deterministically."""
        entry = TaskHistory.objects.create(
            task=task, field_name="status", old_value="REVIEW", new_value=status,
            changed_by="alice@test.com",
        )
        TaskHistory.objects.filter(id=entry.id).update(changed_at=when)

    def _column_ids(self, *statuses):
        return list(
            self.board.tasks.filter(status__in=statuses)
            .order_by("kanban_position", "id")
            .values_list("id", flat=True)
        )

    def _suppress_auto_advance(self):
        """Context manager that silences auto-advance on REVIEW transitions.

        Tests verifying kanban_position for the REVIEW column need the task to
        stay in REVIEW. In a fresh test DB there are no AGENT users with
        available_models, so the production path skips reflection and runs
        merge+advance → TESTING immediately. Patch the trigger so the test
        controls the kanban_position outcome cleanly.
        """
        return patch("tasks.views._trigger_auto_reflection", lambda task: None)

    def test_manual_reorder_within_column_persists_exact_index(self):
        t1 = self.make_task(self.board, title="t1", status="TODO", kanban_position=0)
        t2 = self.make_task(self.board, title="t2", status="TODO", kanban_position=1)
        t3 = self.make_task(self.board, title="t3", status="TODO", kanban_position=2)

        resp = self.client.put(
            f"/tasks/{t3.id}/",
            {"updated_by": "alice@test.com", "kanban_target_index": 0},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._column_ids("TODO"), [t3.id, t1.id, t2.id])

    def test_manual_cross_column_move_uses_requested_index(self):
        t1 = self.make_task(self.board, title="todo-1", status="TODO", kanban_position=0)
        t2 = self.make_task(self.board, title="todo-2", status="TODO", kanban_position=1)
        r1 = self.make_task(self.board, title="review-1", status="REVIEW", kanban_position=0)

        with self._suppress_auto_advance():
            resp = self.client.put(
                f"/tasks/{t2.id}/",
                {"updated_by": "alice@test.com", "status": "REVIEW", "kanban_target_index": 1},
                format="json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._column_ids("TODO"), [t1.id])
        self.assertEqual(self._column_ids("REVIEW"), [r1.id, t2.id])

    def test_system_cross_column_move_auto_inserts_top(self):
        t1 = self.make_task(self.board, title="todo-1", status="TODO", kanban_position=0)
        r1 = self.make_task(self.board, title="review-1", status="REVIEW", kanban_position=0)
        r2 = self.make_task(self.board, title="review-2", status="REVIEW", kanban_position=1)

        with self._suppress_auto_advance():
            resp = self.client.put(
                f"/tasks/{t1.id}/",
                {"updated_by": "alice@test.com", "status": "REVIEW"},
                format="json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._column_ids("REVIEW"), [t1.id, r1.id, r2.id])

    def test_stop_execution_auto_inserts_top_of_destination(self):
        from unittest.mock import patch

        todo1 = self.make_task(self.board, title="todo-1", status="TODO", kanban_position=0)
        todo2 = self.make_task(self.board, title="todo-2", status="TODO", kanban_position=1)
        executing = self.make_task(
            self.board,
            title="exec",
            status="EXECUTING",
            assignee=self.user,
            kanban_position=0,
            metadata={"active_execution": {"run_token": "run_1", "pid": 1234}},
        )

        with patch("tasks.views._attempt_odin_stop", return_value={"ok": True, "engine": "mock-stop"}):
            resp = self.client.post(
                f"/tasks/{executing.id}/stop_execution/",
                {"updated_by": "alice@test.com", "target_status": "TODO"},
                format="json",
            )
        self.assertEqual(resp.status_code, 200)

        executing.refresh_from_db()
        self.assertEqual(executing.status, "TODO")
        self.assertEqual(self._column_ids("TODO"), [executing.id, todo1.id, todo2.id])

    def test_execution_result_auto_inserts_top_of_review(self):
        review1 = self.make_task(self.board, title="review-1", status="REVIEW", kanban_position=0)
        review2 = self.make_task(self.board, title="review-2", status="REVIEW", kanban_position=1)
        executing = self.make_task(self.board, title="exec", status="EXECUTING", kanban_position=0)

        with self._suppress_auto_advance():
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
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._column_ids("REVIEW"), [executing.id, review1.id, review2.id])

    def test_kanban_endpoint_uses_kanban_position_order(self):
        todo1 = self.make_task(self.board, title="todo-1", status="TODO", kanban_position=1)
        todo2 = self.make_task(self.board, title="todo-2", status="TODO", kanban_position=0)
        review1 = self.make_task(self.board, title="review-1", status="REVIEW", kanban_position=1)
        review2 = self.make_task(self.board, title="review-2", status="REVIEW", kanban_position=0)

        resp = self.client.get(f"/api/kanban/?board_id={self.board.id}")
        self.assertEqual(resp.status_code, 200)

        columns = resp.data["columns"]
        todo_ids = [row["id"] for row in columns["TODO"]["tasks"]]
        review_ids = [row["id"] for row in columns["REVIEW"]["tasks"]]
        self.assertEqual(todo_ids, [todo2.id, todo1.id])
        self.assertEqual(review_ids, [review2.id, review1.id])

    def test_kanban_initial_returns_columns_with_total_count(self):
        for i in range(25):
            self.make_task(self.board, title=f"todo-{i}", status="TODO", kanban_position=i)
        self.make_task(self.board, title="review-1", status="REVIEW", kanban_position=0)

        resp = self.client.get(f"/api/kanban/?board_id={self.board.id}")
        self.assertEqual(resp.status_code, 200)
        columns = resp.data["columns"]

        # TODO has 25 tasks but only 20 returned
        self.assertEqual(columns["TODO"]["total_count"], 25)
        self.assertEqual(len(columns["TODO"]["tasks"]), 20)

        # REVIEW has 1 task
        self.assertEqual(columns["REVIEW"]["total_count"], 1)
        self.assertEqual(len(columns["REVIEW"]["tasks"]), 1)

        # Empty columns have 0
        self.assertEqual(columns["BACKLOG"]["total_count"], 0)
        self.assertEqual(len(columns["BACKLOG"]["tasks"]), 0)

    def test_kanban_per_status_limit_respected(self):
        for i in range(10):
            self.make_task(self.board, title=f"todo-{i}", status="TODO", kanban_position=i)

        resp = self.client.get(f"/api/kanban/?board_id={self.board.id}&per_status_limit=5")
        self.assertEqual(resp.status_code, 200)
        columns = resp.data["columns"]
        self.assertEqual(columns["TODO"]["total_count"], 10)
        self.assertEqual(len(columns["TODO"]["tasks"]), 5)

    def test_kanban_load_more_returns_offset_slice(self):
        for i in range(30):
            self.make_task(self.board, title=f"todo-{i}", status="TODO", kanban_position=i)

        resp = self.client.get(
            f"/api/kanban/?board_id={self.board.id}&status=TODO&offset=20&limit=20"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["total_count"], 30)
        self.assertEqual(len(resp.data["tasks"]), 10)
        self.assertFalse(resp.data["has_more"])

    def test_kanban_load_more_has_more_flag(self):
        for i in range(50):
            self.make_task(self.board, title=f"todo-{i}", status="TODO", kanban_position=i)

        resp = self.client.get(
            f"/api/kanban/?board_id={self.board.id}&status=TODO&offset=0&limit=20"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["total_count"], 50)
        self.assertEqual(len(resp.data["tasks"]), 20)
        self.assertTrue(resp.data["has_more"])

    def test_kanban_in_progress_includes_executing(self):
        self.make_task(self.board, title="ip-1", status="IN_PROGRESS", kanban_position=0)
        self.make_task(self.board, title="exec-1", status="EXECUTING", kanban_position=1)

        resp = self.client.get(f"/api/kanban/?board_id={self.board.id}")
        self.assertEqual(resp.status_code, 200)
        columns = resp.data["columns"]
        self.assertEqual(columns["IN_PROGRESS"]["total_count"], 2)
        self.assertEqual(len(columns["IN_PROGRESS"]["tasks"]), 2)

    def test_kanban_load_more_invalid_status_returns_400(self):
        resp = self.client.get(
            f"/api/kanban/?board_id={self.board.id}&status=INVALID"
        )
        self.assertEqual(resp.status_code, 400)

    def test_testing_column_orders_by_most_recently_landed(self):
        """TESTING must order by recency, not kanban_position — kanban_position
        only happens to correlate with recency because system moves insert
        at the top; a deliberate ordering key must not depend on that."""
        now = timezone.now()
        old = self.make_task(self.board, title="old", status="TESTING", kanban_position=0)
        mid = self.make_task(self.board, title="mid", status="TESTING", kanban_position=1)
        recent = self.make_task(self.board, title="recent", status="TESTING", kanban_position=2)
        # kanban_position order (0,1,2) is the OPPOSITE of landing order —
        # if the endpoint were still using kanban_position, this would fail.
        self._land(old, "TESTING", now - timedelta(hours=3))
        self._land(mid, "TESTING", now - timedelta(hours=2))
        self._land(recent, "TESTING", now - timedelta(hours=1))

        resp = self.client.get(f"/api/kanban/?board_id={self.board.id}")
        self.assertEqual(resp.status_code, 200)
        ids = [row["id"] for row in resp.data["columns"]["TESTING"]["tasks"]]
        self.assertEqual(ids, [recent.id, mid.id, old.id])

    def test_done_column_orders_by_most_recently_landed(self):
        now = timezone.now()
        old = self.make_task(self.board, title="old", status="DONE", kanban_position=5)
        recent = self.make_task(self.board, title="recent", status="DONE", kanban_position=0)
        self._land(old, "DONE", now - timedelta(days=1))
        self._land(recent, "DONE", now - timedelta(minutes=5))

        resp = self.client.get(f"/api/kanban/?board_id={self.board.id}")
        self.assertEqual(resp.status_code, 200)
        ids = [row["id"] for row in resp.data["columns"]["DONE"]["tasks"]]
        self.assertEqual(ids, [recent.id, old.id])

    def test_manual_drag_within_testing_does_not_change_recency_order(self):
        """Dragging a card within TESTING moves kanban_position but must not
        change the read order — recency ordering is explicit and immune to
        incidental drag-position side effects."""
        now = timezone.now()
        old = self.make_task(self.board, title="old", status="TESTING", kanban_position=1)
        recent = self.make_task(self.board, title="recent", status="TESTING", kanban_position=0)
        self._land(old, "TESTING", now - timedelta(hours=2))
        self._land(recent, "TESTING", now - timedelta(hours=1))

        # Manually drag `old` to the top of the column (position 0).
        resp = self.client.put(
            f"/tasks/{old.id}/",
            {"updated_by": "alice@test.com", "kanban_target_index": 0},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        resp = self.client.get(f"/api/kanban/?board_id={self.board.id}")
        ids = [row["id"] for row in resp.data["columns"]["TESTING"]["tasks"]]
        self.assertEqual(ids, [recent.id, old.id])

    def test_kanban_load_more_preserves_recency_order_for_testing(self):
        now = timezone.now()
        tasks = [
            self.make_task(self.board, title=f"t{i}", status="TESTING", kanban_position=i)
            for i in range(25)
        ]
        for i, task in enumerate(tasks):
            # Higher index landed more recently.
            self._land(task, "TESTING", now - timedelta(hours=25 - i))

        first_page = self.client.get(
            f"/api/kanban/?board_id={self.board.id}&status=TESTING&offset=0&limit=20"
        )
        second_page = self.client.get(
            f"/api/kanban/?board_id={self.board.id}&status=TESTING&offset=20&limit=20"
        )
        expected_order = [t.id for t in reversed(tasks)]
        got_order = (
            [row["id"] for row in first_page.data["tasks"]]
            + [row["id"] for row in second_page.data["tasks"]]
        )
        self.assertEqual(got_order, expected_order)


class TestExecutionResultRepositionLockResilience(APITestCase):
    """Concurrent execution_result posts must not 500 when the kanban
    reposition hits a SQLite 'database is locked' (the failure that lost
    task 323's finished run).

    The kanban reposition is bookkeeping relative to accepting the result:
    losing a position is fine, losing a run result is not. So the
    execution_result endpoint must accept the result even when the
    reposition is locked. (See docs/patterns/bookkeeping-never-kills-the-run.md.)
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def _suppress_auto_advance(self):
        return patch("tasks.views._trigger_auto_reflection", lambda task: None)

    def test_execution_result_accepted_when_reposition_locked(self):
        """A locked kanban reposition must not turn into a 500 that loses
        the run result. The status change, history, and comment must still
        land — only the position bookkeeping is skipped."""
        self.make_task(self.board, title="review-1", status="REVIEW", kanban_position=0)
        executing = self.make_task(
            self.board, title="exec", status="EXECUTING", kanban_position=0,
        )

        with self._suppress_auto_advance(), \
                patch("tasks.views.move_task", side_effect=OperationalError("database is locked")):
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

        # The result is accepted even though the reposition was locked.
        self.assertEqual(resp.status_code, 200)
        executing.refresh_from_db()
        self.assertEqual(executing.status, "REVIEW")
        self.assertTrue(
            TaskHistory.objects.filter(
                task=executing, field_name="status", new_value="REVIEW"
            ).exists()
        )
        self.assertTrue(TaskComment.objects.filter(task=executing).exists())

    def test_execution_result_accepted_when_reposition_locked_failure_path(self):
        """The reposition on a FAILED path must also be best-effort. A
        non-escalated FAILED result is accepted even when locked."""
        executing = self.make_task(
            self.board, title="exec", status="EXECUTING", kanban_position=0,
        )

        with patch("tasks.views.move_task", side_effect=OperationalError("database is locked")):
            resp = self.client.post(
                f"/tasks/{executing.id}/execution_result/",
                {
                    "execution_result": {
                        "success": False,
                        "raw_output": "boom",
                        "duration_ms": 1200.0,
                        "agent": "mock",
                        "error": "timeout",
                        "metadata": {},
                    },
                    "status": "FAILED",
                    "updated_by": "mock@odin.agent",
                },
                format="json",
            )

        self.assertEqual(resp.status_code, 200)
        executing.refresh_from_db()
        self.assertEqual(executing.status, "FAILED")
        self.assertTrue(TaskComment.objects.filter(task=executing).exists())


class TestMoveTaskRetriesOnLock(APITestCase):
    """move_task (the kanban reposition) retries a transient lock instead
    of failing on the first collision — the bounded retry layer that sits
    beneath the best-effort decoupling."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    @patch("tasks.db.time.sleep")
    def test_move_task_retries_locked_write_then_succeeds(self, _sleep):
        """The reposition recovers from a transient 'database is locked'
        without surfacing the error to its caller."""
        from tasks import kanban_ordering
        from tasks.kanban_ordering import move_task

        self.make_task(self.board, title="r1", status="REVIEW", kanban_position=0)
        t = self.make_task(
            self.board, title="t", status="EXECUTING", kanban_position=0,
        )

        real_persist = kanban_ordering._persist_dense_positions
        raised = {"v": False}

        def flaky_persist(tasks):
            if not raised["v"]:
                raised["v"] = True
                raise OperationalError("database is locked")
            return real_persist(tasks)

        with patch("tasks.kanban_ordering._persist_dense_positions", side_effect=flaky_persist):
            pos = move_task(t, target_status="REVIEW", target_index=None)

        # The lock was hit and recovered from.
        self.assertTrue(raised["v"])
        # The task still lands at the top of its new column. move_task only
        # repositions (it does not change task.status — the caller does).
        self.assertEqual(pos, 0)
        t.refresh_from_db()
        self.assertEqual(t.kanban_position, 0)
