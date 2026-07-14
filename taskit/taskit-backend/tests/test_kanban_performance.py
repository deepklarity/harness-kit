"""Kanban board endpoint performance regression tests.

The board page was slow because TaskListSerializer (used for every card in
every lane) computed cost/usage fields that require an unbounded comment
scan per task, plus separate history queries for time-in-status — none of
which the Kanban card actually renders (see TaskCard.tsx). That made the
initial `/api/kanban/` fetch issue O(tasks) extra queries instead of O(1).

These tests seed a board with a full page of tasks in every lane (the
"130+ tasks" scenario from the bug report) and assert the query count stays
bounded regardless of how much history/comment/reflection data each task
carries.
"""

import time

from django.db import connection
from django.test.utils import CaptureQueriesContext

from .base import APITestCase

from tasks.models import ReflectionReport, ReflectionStatus, Task, TaskComment, TaskHistory


class TestKanbanPerformance(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.user = self.make_user()

    def _seed_heavy_task(self, status, position, index):
        """A task shaped like a real, long-lived production task.

        history: enough status transitions to exercise time_in_statuses.
        comments: several, with one holding a large trace payload — the
        thing that used to make ``get_usage`` parse JSONL per task.
        reflections: a completed one, to exercise reflection cost lookups
        if a serializer still walks them.
        """
        task = self.make_task(
            self.board,
            title=f"{status.lower()}-{index}",
            status=status,
            kanban_position=position,
            assignee=self.user,
        )
        base_statuses = ["TODO", "IN_PROGRESS", "REVIEW", status]
        for i, new_value in enumerate(base_statuses):
            TaskHistory.objects.create(
                task=task,
                field_name="status",
                old_value=base_statuses[i - 1] if i else "",
                new_value=new_value,
                changed_by="alice@test.com",
            )

        trace_line = (
            '{"type":"result","result":"done","stats":{"input_tokens":1200,"output_tokens":340}}\n'
        )
        TaskComment.objects.create(
            task=task,
            author_email="agent@odin.agent",
            content=trace_line * 50,
            attachments=["trace:execution_jsonl"],
        )
        for i in range(3):
            TaskComment.objects.create(
                task=task,
                author_email="alice@test.com",
                content=f"note {i}",
            )

        ReflectionReport.objects.create(
            task=task,
            reviewer_agent="claude",
            reviewer_model="claude-sonnet-5",
            requested_by="alice@test.com",
            status=ReflectionStatus.COMPLETED,
            verdict="PASS",
            token_usage={"input_tokens": 500, "output_tokens": 100},
        )
        return task

    STATUSES = ["BACKLOG", "TODO", "IN_PROGRESS", "REVIEW", "TESTING", "DONE", "FAILED"]

    def _seed_board(self, per_lane):
        for status in self.STATUSES:
            for i in range(per_lane):
                self._seed_heavy_task(status, position=i, index=i)

    def _seed_full_board(self):
        self._seed_board(per_lane=20)

    def test_kanban_initial_query_count_independent_of_task_count(self):
        """Initial kanban fetch must not scale with the number of tasks.

        All 7 lanes populated with 3 heavy tasks each must cost the same
        number of queries as all 7 lanes populated with 20 heavy tasks each
        (a full page, the "130+ tasks" scenario from the bug report) — the
        query count is a function of the number of *lanes*, not the number
        of *tasks* in them, once each lane is served by one prefetch-backed
        query instead of per-task lookups.
        """
        self.board_small = self.make_board(name="Small")
        self.board = self.board_small
        self._seed_board(per_lane=3)
        with CaptureQueriesContext(connection) as small:
            resp_small = self.client.get(f"/api/kanban/?board_id={self.board_small.id}")
        self.assertEqual(resp_small.status_code, 200)

        self.board_large = self.make_board(name="Large")
        self.board = self.board_large
        self._seed_full_board()
        with CaptureQueriesContext(connection) as large:
            resp_large = self.client.get(f"/api/kanban/?board_id={self.board_large.id}")
        self.assertEqual(resp_large.status_code, 200)
        columns = resp_large.data["columns"]
        self.assertEqual(len(columns["TODO"]["tasks"]), 20)
        self.assertEqual(len(columns["DONE"]["tasks"]), 20)

        self.assertEqual(
            len(small.captured_queries), len(large.captured_queries),
            "kanban query count must not grow with task count — got "
            f"{len(small.captured_queries)} queries for 21 tasks vs "
            f"{len(large.captured_queries)} for 140 tasks",
        )

    def test_kanban_card_omits_detail_only_fields(self):
        """Cost/usage/reference-image fields belong to the task detail modal,
        not the board — TaskCard never renders them (see TaskCard.tsx)."""
        self._seed_heavy_task("TESTING", position=0, index=0)

        resp = self.client.get(f"/api/kanban/?board_id={self.board.id}")
        self.assertEqual(resp.status_code, 200)
        card = resp.data["columns"]["TESTING"]["tasks"][0]
        for field in ("usage", "estimated_cost_usd", "reflection_cost_usd", "reference_images"):
            self.assertNotIn(field, card)

        # Fields the card *does* render must still be present and correct.
        self.assertIn("time_in_statuses", card)
        self.assertIn("comment_count", card)
        self.assertEqual(card["comment_count"], 4)

    def test_kanban_benchmark_wall_clock(self):
        """Not a pass/fail gate — prints wall-clock timing for the proof doc."""
        self._seed_full_board()
        start = time.perf_counter()
        resp = self.client.get(f"/api/kanban/?board_id={self.board.id}")
        elapsed_ms = (time.perf_counter() - start) * 1000
        self.assertEqual(resp.status_code, 200)
        print(f"\n[BENCH] /api/kanban/ with 140 heavy tasks: {elapsed_ms:.1f}ms")
