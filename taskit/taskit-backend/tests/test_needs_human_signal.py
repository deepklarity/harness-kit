"""Backend contract for the card-level needs-human signal.

The kanban/list payload must expose ``needs_human`` (bool) and
``needs_human_reason`` (short string) so the board card can flag tasks
awaiting a human decision without opening the modal. Derived from:

  * ``metadata.merge_status == "needs_human"``  (merge conflict / stall)
  * ``metadata.has_pending_question`` is True    (unanswered question)
  * latest reflection ``verdict == "ERROR"``     (reviewer failed → triage)
  * ``metadata.dispatch_blocked_reason`` set      (dispatch guardrail)
"""

from .base import APITestCase
from tasks.models import ReflectionReport, Task


class TestNeedsHumanSignal(APITestCase):

    def setUp(self):
        super().setUp()
        self.board = self.make_board(name="Board")

        self.normal = self.make_task(self.board, title="Normal task")

        self.merge_human = self.make_task(
            self.board, title="Merge needs human", status="REVIEW",
        )
        Task.objects.filter(pk=self.merge_human.pk).update(
            metadata={"merge_status": "needs_human"},
        )

        self.question = self.make_task(
            self.board, title="Question pending", status="EXECUTING",
        )
        Task.objects.filter(pk=self.question.pk).update(
            metadata={"has_pending_question": True},
        )

        self.errored = self.make_task(
            self.board, title="Review errored", status="REVIEW",
        )
        ReflectionReport.objects.create(
            task=self.errored,
            reviewer_agent="senior",
            reviewer_model="some-model",
            requested_by="reviewer@test.com",
            status="COMPLETED",
            verdict="ERROR",
        )

        self.dispatched = self.make_task(
            self.board, title="Dispatch blocked", status="TODO",
        )
        Task.objects.filter(pk=self.dispatched.pk).update(
            metadata={"dispatch_blocked_reason": "no_assignee"},
        )

    def _kanban_task(self, task):
        resp = self.client.get(f"/api/kanban/?board_id={self.board.id}")
        self.assertEqual(resp.status_code, 200)
        for col in resp.data["columns"].values():
            for serialized in col["tasks"]:
                if serialized["id"] == task.id:
                    return serialized
        self.fail(f"task {task.id} not present in kanban payload")

    def test_normal_task_is_not_needs_human(self):
        serialized = self._kanban_task(self.normal)
        self.assertFalse(serialized["needs_human"])
        self.assertEqual(serialized["needs_human_reason"], "")

    def test_merge_needs_human_signal(self):
        serialized = self._kanban_task(self.merge_human)
        self.assertTrue(serialized["needs_human"])
        self.assertIn("merge", serialized["needs_human_reason"].lower())

    def test_pending_question_signal(self):
        serialized = self._kanban_task(self.question)
        self.assertTrue(serialized["needs_human"])
        self.assertIn("question", serialized["needs_human_reason"].lower())

    def test_review_errored_signal(self):
        serialized = self._kanban_task(self.errored)
        self.assertTrue(serialized["needs_human"])
        self.assertIn("review", serialized["needs_human_reason"].lower())

    def test_dispatch_blocked_signal(self):
        serialized = self._kanban_task(self.dispatched)
        self.assertTrue(serialized["needs_human"])
        self.assertIn("dispatch", serialized["needs_human_reason"].lower())

    def test_merge_signal_takes_priority_over_question(self):
        Task.objects.filter(pk=self.merge_human.pk).update(
            metadata={"merge_status": "needs_human", "has_pending_question": True},
        )
        serialized = self._kanban_task(self.merge_human)
        self.assertTrue(serialized["needs_human"])
        self.assertIn("merge", serialized["needs_human_reason"].lower())

    def test_error_verdict_is_not_flagged_when_superseded(self):
        # A newer PASS reflection means the task was re-reviewed; the stale
        # ERROR no longer needs a human.
        ReflectionReport.objects.create(
            task=self.errored,
            reviewer_agent="senior",
            reviewer_model="some-model",
            requested_by="reviewer@test.com",
            status="COMPLETED",
            verdict="PASS",
        )
        serialized = self._kanban_task(self.errored)
        self.assertFalse(serialized["needs_human"])

    def test_tasks_list_endpoint_also_exposes_signal(self):
        resp = self.client.get(f"/tasks/?board_id={self.board.id}")
        self.assertEqual(resp.status_code, 200)
        by_id = {item["id"]: item for item in self.results(resp)}
        self.assertIn(self.merge_human.id, by_id)
        self.assertTrue(by_id[self.merge_human.id]["needs_human"])
        self.assertFalse(by_id[self.normal.id]["needs_human"])
