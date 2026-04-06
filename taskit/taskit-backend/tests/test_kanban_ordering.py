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
