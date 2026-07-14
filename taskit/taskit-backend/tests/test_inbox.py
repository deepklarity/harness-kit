"""Tests for the board inbox: GET /boards/<id>/inbox/.

Everything waiting on a human, in one call: parked merge conflicts
(``metadata.merge_status == "needs_human"``), reversibility parks
(``metadata.hard_action_status == "needs_human"``, task #244), the
TESTING shelf (tasks waiting for a human to flip them to DONE), and
open ErrorEvents (task #222's disposition ledger). Backs the /factory
Inbox region (task #258) so a human can act on each item without
leaving the page.

The endpoint does not exist yet (BoardViewSet has no ``inbox`` action).
These tests are written against the spec in task #258 and are expected
to fail with 404 until the action, view logic, and builder are added.

Response contract (200 OK, all keys always present even when empty):

    {
      "board_id": <int>,
      "board_name": <str>,
      "parked_merges": [{"task_id", "task_title", "why",
                          "question_comment_id", "conflicting_files"}],
      "reversibility_parks": [{"task_id", "task_title", "why",
                                "question_comment_id", "action_key", "branch"}],
      "testing_shelf": [{"task_id", "task_title"}],
      "open_errors": [{"source", "signature", "latest_symptom", "count",
                        "event_ids", "latest_at", "task_id"}],
      "counts": {"parked_merges", "reversibility_parks", "testing_shelf",
                 "open_errors"},
    }
"""
import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from tasks.models import CommentType, ErrorEvent, TaskComment, TaskStatus
from tests.base import APITestCase


class InboxNotFoundTests(APITestCase):
    def test_inbox_not_found(self):
        resp = self.client.get("/boards/999999/inbox/")
        self.assertEqual(resp.status_code, 404)


class InboxEmptyBoardTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def test_empty_board_shape(self):
        resp = self.client.get(f"/boards/{self.board.id}/inbox/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["board_id"], self.board.id)
        self.assertEqual(resp.data["board_name"], self.board.name)
        self.assertEqual(resp.data["parked_merges"], [])
        self.assertEqual(resp.data["reversibility_parks"], [])
        self.assertEqual(resp.data["testing_shelf"], [])
        self.assertEqual(resp.data["open_errors"], [])
        self.assertEqual(
            resp.data["counts"],
            {
                "failed_tasks": 0,
                "parked_merges": 0,
                "reversibility_parks": 0,
                "testing_shelf": 0,
                "open_errors": 0,
            },
        )


class InboxParkedMergesTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(
            self.board, title="Add retry logic",
            status=TaskStatus.REVIEW,
            metadata={"merge_status": "needs_human", "merge_ambiguous_files": ["a.py", "b.py"]},
        )

    def test_parked_merge_appears_with_why_and_comment_id(self):
        question = TaskComment.objects.create(
            task=self.task, author_email="merge-agent@odin", author_label="merge-agent",
            content="Both branches touch settings.py — which wins?\nDetail line.",
            comment_type=CommentType.QUESTION,
        )
        resp = self.client.get(f"/boards/{self.board.id}/inbox/")
        self.assertEqual(resp.status_code, 200)
        parked = resp.data["parked_merges"]
        self.assertEqual(len(parked), 1)
        entry = parked[0]
        self.assertEqual(entry["task_id"], self.task.id)
        self.assertEqual(entry["task_title"], "Add retry logic")
        self.assertEqual(entry["why"], "Both branches touch settings.py — which wins?")
        self.assertEqual(entry["question_comment_id"], question.id)
        self.assertEqual(entry["conflicting_files"], ["a.py", "b.py"])
        self.assertEqual(resp.data["counts"]["parked_merges"], 1)

    def test_parked_merge_without_question_comment_has_empty_why(self):
        resp = self.client.get(f"/boards/{self.board.id}/inbox/")
        entry = resp.data["parked_merges"][0]
        self.assertEqual(entry["why"], "")
        self.assertIsNone(entry["question_comment_id"])

    def test_resolved_merge_excluded(self):
        self.task.metadata["merge_status"] = "merged"
        self.task.save(update_fields=["metadata"])
        resp = self.client.get(f"/boards/{self.board.id}/inbox/")
        self.assertEqual(resp.data["parked_merges"], [])

    def test_parked_merge_on_other_board_excluded(self):
        other_board = self.make_board(name="Other board")
        resp = self.client.get(f"/boards/{other_board.id}/inbox/")
        self.assertEqual(resp.data["parked_merges"], [])


class InboxReversibilityParksTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(
            self.board, title="Cleanup task worktree",
            metadata={
                "hard_action_status": "needs_human",
                "hard_action": {"key": "delete_branch_with_unmerged_work", "branch": "task/sp1/42"},
            },
        )

    def test_reversibility_park_appears_with_expected_fields(self):
        question = TaskComment.objects.create(
            task=self.task, author_email="merge-agent@odin", author_label="merge-agent",
            content="Hard action parked — needs a human decision:\nDelete branch task/sp1/42?",
            comment_type=CommentType.QUESTION,
        )
        resp = self.client.get(f"/boards/{self.board.id}/inbox/")
        self.assertEqual(resp.status_code, 200)
        parks = resp.data["reversibility_parks"]
        self.assertEqual(len(parks), 1)
        entry = parks[0]
        self.assertEqual(entry["task_id"], self.task.id)
        self.assertEqual(entry["task_title"], "Cleanup task worktree")
        self.assertEqual(entry["why"], "Hard action parked — needs a human decision:")
        self.assertEqual(entry["question_comment_id"], question.id)
        self.assertEqual(entry["action_key"], "delete_branch_with_unmerged_work")
        self.assertEqual(entry["branch"], "task/sp1/42")
        self.assertEqual(resp.data["counts"]["reversibility_parks"], 1)

    def test_resolved_reversibility_park_excluded(self):
        self.task.metadata.pop("hard_action_status")
        self.task.save(update_fields=["metadata"])
        resp = self.client.get(f"/boards/{self.board.id}/inbox/")
        self.assertEqual(resp.data["reversibility_parks"], [])


class InboxTestingShelfTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def test_testing_task_appears_on_shelf(self):
        task = self.make_task(self.board, title="Ship it", status=TaskStatus.TESTING)
        resp = self.client.get(f"/boards/{self.board.id}/inbox/")
        self.assertEqual(resp.status_code, 200)
        shelf = resp.data["testing_shelf"]
        self.assertEqual(len(shelf), 1)
        self.assertEqual(shelf[0]["task_id"], task.id)
        self.assertEqual(shelf[0]["task_title"], "Ship it")
        self.assertEqual(resp.data["counts"]["testing_shelf"], 1)

    def test_non_testing_tasks_excluded_from_shelf(self):
        self.make_task(self.board, title="todo", status=TaskStatus.TODO)
        self.make_task(self.board, title="done", status=TaskStatus.DONE)
        resp = self.client.get(f"/boards/{self.board.id}/inbox/")
        self.assertEqual(resp.data["testing_shelf"], [])

    def test_testing_task_on_other_board_excluded(self):
        other_board = self.make_board(name="Other board")
        self.make_task(other_board, title="theirs", status=TaskStatus.TESTING)
        resp = self.client.get(f"/boards/{self.board.id}/inbox/")
        self.assertEqual(resp.data["testing_shelf"], [])


class InboxOpenErrorsTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board, title="Errorful task")

    def test_open_error_appears_with_task_id(self):
        evt = ErrorEvent.objects.create(
            source=ErrorEvent.SOURCE_MERGE_FAILURE,
            source_id="err-1",
            symptom="Conflict in foo.py",
            symptom_signature="merge_conflict:foo.py",
            disposition=ErrorEvent.DISPOSITION_OPEN,
            task=self.task,
        )
        resp = self.client.get(f"/boards/{self.board.id}/inbox/")
        self.assertEqual(resp.status_code, 200)
        errors = resp.data["open_errors"]
        self.assertEqual(len(errors), 1)
        entry = errors[0]
        self.assertEqual(entry["signature"], "merge_conflict:foo.py")
        self.assertEqual(entry["count"], 1)
        self.assertEqual(entry["event_ids"], [evt.id])
        self.assertEqual(entry["task_id"], self.task.id)
        self.assertEqual(resp.data["counts"]["open_errors"], 1)

    def test_fixed_disposition_excluded(self):
        ErrorEvent.objects.create(
            source=ErrorEvent.SOURCE_MERGE_FAILURE,
            source_id="err-fixed",
            symptom="Old fixed issue",
            symptom_signature="old_fixed_issue",
            disposition=ErrorEvent.DISPOSITION_FIXED,
            task=self.task,
        )
        resp = self.client.get(f"/boards/{self.board.id}/inbox/")
        self.assertEqual(resp.data["open_errors"], [])

    def test_error_on_other_board_excluded(self):
        other_board = self.make_board(name="Other board")
        other_task = self.make_task(other_board, title="Other task")
        ErrorEvent.objects.create(
            source=ErrorEvent.SOURCE_MERGE_FAILURE,
            source_id="err-other",
            symptom="Other board conflict",
            symptom_signature="other_board_conflict",
            disposition=ErrorEvent.DISPOSITION_OPEN,
            task=other_task,
        )
        resp = self.client.get(f"/boards/{self.board.id}/inbox/")
        signatures = {e["signature"] for e in resp.data["open_errors"]}
        self.assertNotIn("other_board_conflict", signatures)
