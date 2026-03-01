from .base import APITestCase


class TestKanbanOrdering(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.user = self.make_user()

    def _column_ids(self, *statuses):
        return list(
            self.board.tasks.filter(status__in=statuses)
            .order_by("kanban_position", "id")
            .values_list("id", flat=True)
        )

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

        todo_ids = [row["id"] for row in resp.data if row["status"] == "TODO"]
        review_ids = [row["id"] for row in resp.data if row["status"] == "REVIEW"]
        self.assertEqual(todo_ids, [todo2.id, todo1.id])
        self.assertEqual(review_ids, [review2.id, review1.id])
