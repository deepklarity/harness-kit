"""Tests for the board "factory" snapshot: GET /boards/<id>/factory/.

A single-call, at-a-glance operator view of a board's live state: what's
running right now, how the queues are stacked, what merged recently, what
error signatures are still open, and a one-line story headline. This is
the endpoint a dashboard widget or CLI status check hits instead of
stitching together /tasks/, /errors/, and /story/ separately.

The endpoint does not exist yet (BoardViewSet has no ``factory`` action).
These tests are written against the spec in task #240 and are expected to
fail with 404 until the action, view logic, and serializer are added.

Response contract (200 OK, all keys always present even when empty):

    {
      "board_id": <int>,
      "board_name": <str>,
      "running": [{"task_id", "task_title", "run_token", "state",
                    "started_at", "last_heartbeat", "seconds_since_heartbeat"}],
      "queues": {"waiting", "executing", "review", "shelf"},
      "recent_merges": [{"id", "task_id", "task_title", "mode", "outcome",
                          "trigger", "started_at", "finished_at", "lag_seconds"}],
      "open_errors": [{"source", "signature", "latest_symptom",
                        "latest_disposition", "count", "event_ids", "latest_at"}],
      "story": {"headline", "tldr": {"landed", "hands_free", "incidents",
                                       "waiting_on_human"}, "event_count"},
    }

Queue buckets: waiting = BACKLOG+TODO, executing = IN_PROGRESS+EXECUTING,
review = REVIEW, shelf = TESTING (the "resting shelf" — DONE/FAILED are
terminal and not counted in any bucket).
"""
import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from datetime import timedelta

from django.utils import timezone

from tasks.models import (
    ErrorEvent,
    MergeAttempt,
    MergeMode,
    MergeOutcome,
    MergeTrigger,
    TaskRun,
    TaskRunState,
    TaskStatus,
)
from tests.base import APITestCase


class FactorySnapshotNotFoundTests(APITestCase):
    def test_factory_not_found(self):
        resp = self.client.get("/boards/999999/factory/")
        self.assertEqual(resp.status_code, 404)


class FactorySnapshotEmptyBoardTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def test_empty_board_shape(self):
        resp = self.client.get(f"/boards/{self.board.id}/factory/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["board_id"], self.board.id)
        self.assertEqual(resp.data["board_name"], self.board.name)
        self.assertEqual(resp.data["running"], [])
        self.assertEqual(
            resp.data["queues"],
            {"waiting": 0, "executing": 0, "review": 0, "shelf": 0},
        )
        self.assertEqual(resp.data["recent_merges"], [])
        self.assertEqual(resp.data["open_errors"], [])
        self.assertIsNone(resp.data["story"]["headline"])
        self.assertEqual(resp.data["story"]["event_count"], 0)


class FactorySnapshotRunningTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(
            self.board, title="Fix login bug", status=TaskStatus.IN_PROGRESS,
        )

    def test_running_task_appears_with_expected_fields(self):
        run = TaskRun.objects.create(
            task=self.task, run_token="abc123", state=TaskRunState.RUNNING,
        )
        resp = self.client.get(f"/boards/{self.board.id}/factory/")
        self.assertEqual(resp.status_code, 200)
        running = resp.data["running"]
        self.assertEqual(len(running), 1)
        entry = running[0]
        self.assertEqual(entry["task_id"], self.task.id)
        self.assertEqual(entry["task_title"], self.task.title)
        self.assertEqual(entry["run_token"], run.run_token)
        self.assertEqual(entry["state"], TaskRunState.RUNNING)
        self.assertIsNotNone(entry["started_at"])
        self.assertIsNotNone(entry["last_heartbeat"])
        self.assertIsInstance(entry["seconds_since_heartbeat"], (int, float))
        self.assertGreaterEqual(entry["seconds_since_heartbeat"], 0)

    def test_finished_run_excluded(self):
        TaskRun.objects.create(
            task=self.task, run_token="finished-tok", state=TaskRunState.FINISHED,
            finished_at=timezone.now(),
        )
        resp = self.client.get(f"/boards/{self.board.id}/factory/")
        self.assertEqual(resp.data["running"], [])

    def test_expired_run_excluded(self):
        TaskRun.objects.create(
            task=self.task, run_token="expired-tok", state=TaskRunState.EXPIRED,
            finished_at=timezone.now(),
        )
        resp = self.client.get(f"/boards/{self.board.id}/factory/")
        self.assertEqual(resp.data["running"], [])

    def test_killed_run_excluded(self):
        TaskRun.objects.create(
            task=self.task, run_token="killed-tok", state=TaskRunState.KILLED,
            finished_at=timezone.now(),
        )
        resp = self.client.get(f"/boards/{self.board.id}/factory/")
        self.assertEqual(resp.data["running"], [])

    def test_running_run_on_other_board_excluded(self):
        TaskRun.objects.create(
            task=self.task, run_token="abc123", state=TaskRunState.RUNNING,
        )
        other_board = self.make_board(name="Other board")
        other_task = self.make_task(
            other_board, title="Other board task", status=TaskStatus.IN_PROGRESS,
        )
        TaskRun.objects.create(
            task=other_task, run_token="other-tok", state=TaskRunState.RUNNING,
        )
        resp = self.client.get(f"/boards/{self.board.id}/factory/")
        running_task_ids = {e["task_id"] for e in resp.data["running"]}
        self.assertEqual(running_task_ids, {self.task.id})


class FactorySnapshotQueuesTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def test_queue_buckets_count_correctly(self):
        self.make_task(self.board, title="backlog", status=TaskStatus.BACKLOG)
        self.make_task(self.board, title="todo", status=TaskStatus.TODO)
        self.make_task(self.board, title="in_progress", status=TaskStatus.IN_PROGRESS)
        self.make_task(self.board, title="executing", status=TaskStatus.EXECUTING)
        self.make_task(self.board, title="review", status=TaskStatus.REVIEW)
        self.make_task(self.board, title="testing", status=TaskStatus.TESTING)
        self.make_task(self.board, title="done", status=TaskStatus.DONE)
        self.make_task(self.board, title="failed", status=TaskStatus.FAILED)

        resp = self.client.get(f"/boards/{self.board.id}/factory/")
        self.assertEqual(resp.status_code, 200)
        queues = resp.data["queues"]
        self.assertEqual(queues["waiting"], 2)
        self.assertEqual(queues["executing"], 2)
        self.assertEqual(queues["review"], 1)
        self.assertEqual(queues["shelf"], 1)

    def test_queue_buckets_are_board_scoped(self):
        self.make_task(self.board, title="mine", status=TaskStatus.TODO)
        other_board = self.make_board(name="Other board")
        self.make_task(other_board, title="theirs", status=TaskStatus.TODO)

        resp = self.client.get(f"/boards/{self.board.id}/factory/")
        self.assertEqual(resp.data["queues"]["waiting"], 1)


class FactorySnapshotRecentMergesTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board, odin_id="sp_factory")
        self.task = self.make_task(self.board, spec=self.spec, title="Fix login bug")

    def test_recent_merges_ordered_newest_first_with_expected_fields(self):
        now = timezone.now()
        older_started = now - timedelta(minutes=10)
        older_finished = older_started + timedelta(seconds=3)
        newer_started = now - timedelta(minutes=1)
        newer_finished = newer_started + timedelta(seconds=4.2)

        older = MergeAttempt.objects.create(
            task=self.task, spec=self.spec,
            trigger=MergeTrigger.REFLECTION_PASS, mode=MergeMode.STATIC,
            outcome=MergeOutcome.MERGED,
            started_at=older_started, finished_at=older_finished,
        )
        newer = MergeAttempt.objects.create(
            task=self.task, spec=self.spec,
            trigger=MergeTrigger.RETRY, mode=MergeMode.AGENT,
            outcome=MergeOutcome.MERGED,
            started_at=newer_started, finished_at=newer_finished,
        )

        resp = self.client.get(f"/boards/{self.board.id}/factory/")
        self.assertEqual(resp.status_code, 200)
        merges = resp.data["recent_merges"]
        self.assertEqual(len(merges), 2)
        # Newest started_at first.
        self.assertEqual(merges[0]["id"], newer.id)
        self.assertEqual(merges[1]["id"], older.id)

        entry = merges[0]
        self.assertEqual(entry["task_id"], self.task.id)
        self.assertEqual(entry["task_title"], self.task.title)
        self.assertEqual(entry["mode"], MergeMode.AGENT)
        self.assertEqual(entry["outcome"], MergeOutcome.MERGED)
        self.assertEqual(entry["trigger"], MergeTrigger.RETRY)
        expected_lag = (newer_finished - newer_started).total_seconds()
        self.assertAlmostEqual(entry["lag_seconds"], expected_lag, places=2)

    def test_merge_on_other_board_excluded(self):
        MergeAttempt.objects.create(
            task=self.task, spec=self.spec,
            trigger=MergeTrigger.REFLECTION_PASS, mode=MergeMode.STATIC,
            outcome=MergeOutcome.MERGED,
            started_at=timezone.now(), finished_at=timezone.now(),
        )
        other_board = self.make_board(name="Other board")
        other_spec = self.make_spec(other_board, odin_id="sp_other")
        other_task = self.make_task(other_board, spec=other_spec, title="Other task")
        other_merge = MergeAttempt.objects.create(
            task=other_task, spec=other_spec,
            trigger=MergeTrigger.REFLECTION_PASS, mode=MergeMode.STATIC,
            outcome=MergeOutcome.MERGED,
            started_at=timezone.now(), finished_at=timezone.now(),
        )

        resp = self.client.get(f"/boards/{self.board.id}/factory/")
        merge_ids = {m["id"] for m in resp.data["recent_merges"]}
        self.assertNotIn(other_merge.id, merge_ids)


class FactorySnapshotOpenErrorsTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board, odin_id="sp_factory_err")
        self.task = self.make_task(self.board, spec=self.spec, title="Errorful task")

    def test_duplicate_signature_collapses_into_one_entry_with_count(self):
        evt1 = ErrorEvent.objects.create(
            source=ErrorEvent.SOURCE_MERGE_FAILURE,
            source_id="err-1",
            symptom="Conflict in foo.py",
            symptom_signature="merge_conflict:foo.py",
            disposition=ErrorEvent.DISPOSITION_OPEN,
            task=self.task,
        )
        evt2 = ErrorEvent.objects.create(
            source=ErrorEvent.SOURCE_MERGE_FAILURE,
            source_id="err-2",
            symptom="Conflict in foo.py",
            symptom_signature="merge_conflict:foo.py",
            disposition=ErrorEvent.DISPOSITION_OPEN,
            task=self.task,
        )

        resp = self.client.get(f"/boards/{self.board.id}/factory/")
        self.assertEqual(resp.status_code, 200)
        open_errors = resp.data["open_errors"]
        matching = [
            e for e in open_errors if e["signature"] == "merge_conflict:foo.py"
        ]
        self.assertEqual(len(matching), 1)
        entry = matching[0]
        self.assertEqual(entry["source"], ErrorEvent.SOURCE_MERGE_FAILURE)
        self.assertEqual(entry["count"], 2)
        self.assertEqual(set(entry["event_ids"]), {evt1.id, evt2.id})
        self.assertEqual(entry["latest_disposition"], ErrorEvent.DISPOSITION_OPEN)
        self.assertIn("foo.py", entry["latest_symptom"])
        self.assertIsNotNone(entry["latest_at"])

    def test_fixed_disposition_excluded(self):
        ErrorEvent.objects.create(
            source=ErrorEvent.SOURCE_MERGE_FAILURE,
            source_id="err-fixed",
            symptom="Old fixed issue",
            symptom_signature="old_fixed_issue",
            disposition=ErrorEvent.DISPOSITION_FIXED,
            task=self.task,
        )
        resp = self.client.get(f"/boards/{self.board.id}/factory/")
        signatures = {e["signature"] for e in resp.data["open_errors"]}
        self.assertNotIn("old_fixed_issue", signatures)

    def test_error_on_other_board_task_excluded(self):
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
        resp = self.client.get(f"/boards/{self.board.id}/factory/")
        signatures = {e["signature"] for e in resp.data["open_errors"]}
        self.assertNotIn("other_board_conflict", signatures)

    def test_spec_scoped_error_included(self):
        """Errors can be linked via spec= instead of task= and must still
        be attributed to the board through the spec's board FK."""
        ErrorEvent.objects.create(
            source=ErrorEvent.SOURCE_GATE_CRASH,
            source_id="err-gate",
            symptom="verify.sh crashed",
            symptom_signature="gate_crash_verify_sh",
            disposition=ErrorEvent.DISPOSITION_OPEN,
            spec=self.spec,
        )
        resp = self.client.get(f"/boards/{self.board.id}/factory/")
        signatures = {e["signature"] for e in resp.data["open_errors"]}
        self.assertIn("gate_crash_verify_sh", signatures)


class FactorySnapshotStoryTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board, odin_id="sp_factory_story")

    def test_headline_present_when_task_has_landed(self):
        self.make_task(
            self.board, spec=self.spec, title="Landed task", status=TaskStatus.TESTING,
        )
        resp = self.client.get(f"/boards/{self.board.id}/factory/")
        self.assertEqual(resp.status_code, 200)
        story = resp.data["story"]
        self.assertIsInstance(story["headline"], str)
        self.assertGreater(len(story["headline"]), 0)
        self.assertGreaterEqual(story["event_count"], 1)
        tldr = story["tldr"]
        for key in ("landed", "hands_free", "incidents", "waiting_on_human"):
            self.assertIn(key, tldr)
