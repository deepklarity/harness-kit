"""Tests for the board-level story: GET /boards/<id>/story/?since=<iso>.

The board story aggregates events across all specs on a board into a
chronological timeline a renderer or operator can turn into a two-minute
handover. The endpoint (tasks/views.py BoardViewSet.story) and the CLI
diagnostic (testing_tools/board_story.py) share one builder —
tasks/board_story.py — so these tests are the single source of truth for
both surfaces' behavior.

Event kinds: spec_created, task_landed (with hands-free flag), task_failed
(with fingerprint), merge_conflict (with resolution), reflection_escalation,
wave_all_landed.
"""
from datetime import timedelta

from django.utils import timezone

from .base import APITestCase
from tasks.models import (
    CommentType, ReflectionReport, ReflectionStatus, Task, TaskComment,
    TaskHistory, TaskStatus,
)


class BoardStoryEndpointTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board)
        self.alice = self.make_user(name="Alice", email="alice@test.com")

    def test_story_not_found(self):
        resp = self.client.get("/boards/999999/story/")
        self.assertEqual(resp.status_code, 404)

    def test_story_shape_and_empty_board(self):
        resp = self.client.get(f"/boards/{self.board.id}/story/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["board_id"], self.board.id)
        self.assertEqual(resp.data["board_name"], self.board.name)
        self.assertIsNone(resp.data["since"])
        tldr = resp.data["tldr"]
        self.assertEqual(tldr["landed"], 0)
        self.assertEqual(tldr["hands_free"], 0)
        self.assertEqual(tldr["incidents"], 0)
        self.assertEqual(tldr["waiting_on_human"], 0)

    def test_spec_created_event_present(self):
        resp = self.client.get(f"/boards/{self.board.id}/story/")
        kinds = [e["kind"] for e in resp.data["events"]]
        self.assertIn("spec_created", kinds)
        event = next(e for e in resp.data["events"] if e["kind"] == "spec_created")
        self.assertEqual(event["spec_id"], self.spec.id)
        self.assertIn(self.spec.title, event["line"])
        self.assertIsNotNone(event["timestamp"])

    def test_events_ordered_ascending_by_timestamp(self):
        spec2 = self.make_spec(self.board, odin_id="sp_002", title="Second spec")
        resp = self.client.get(f"/boards/{self.board.id}/story/")
        timestamps = [e["timestamp"] for e in resp.data["events"]]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_since_filter_excludes_old_events(self):
        cutoff = timezone.now() + timedelta(seconds=1)
        resp = self.client.get(
            f"/boards/{self.board.id}/story/",
            {"since": cutoff.isoformat()},
        )
        self.assertEqual(resp.data["since"], cutoff.isoformat())
        # spec_created happened before the cutoff → excluded
        kinds = [e["kind"] for e in resp.data["events"]]
        self.assertNotIn("spec_created", kinds)

    def test_task_landed_with_hands_free_flag(self):
        task = self.make_task(
            self.board, title="Landed clean", spec=self.spec, status=TaskStatus.TESTING,
        )
        TaskHistory.objects.create(
            task=task, field_name="status", old_value="TODO", new_value="EXECUTING",
            changed_by="system@taskit",
        )
        TaskHistory.objects.create(
            task=task, field_name="status", old_value="EXECUTING", new_value="TESTING",
            changed_by="system@taskit",
        )
        resp = self.client.get(f"/boards/{self.board.id}/story/")
        landed = [e for e in resp.data["events"] if e["kind"] == "task_landed"]
        self.assertEqual(len(landed), 1)
        self.assertEqual(landed[0]["task_id"], task.id)
        self.assertTrue(landed[0]["hands_free"])
        self.assertIn(task.title, landed[0]["line"])
        self.assertEqual(resp.data["tldr"]["landed"], 1)
        self.assertEqual(resp.data["tldr"]["hands_free"], 1)

    def test_task_landed_not_hands_free_when_operator_commented(self):
        task = self.make_task(
            self.board, title="Operator steered", spec=self.spec, status=TaskStatus.TESTING,
        )
        TaskHistory.objects.create(
            task=task, field_name="status", old_value="TODO", new_value="EXECUTING",
            changed_by="alice@test.com",
        )
        TaskComment.objects.create(
            task=task, author_email="alice@test.com",
            content="Try a different approach", comment_type=CommentType.STATUS_UPDATE,
        )
        TaskHistory.objects.create(
            task=task, field_name="status", old_value="EXECUTING", new_value="TESTING",
            changed_by="system@taskit",
        )
        resp = self.client.get(f"/boards/{self.board.id}/story/")
        landed = [e for e in resp.data["events"] if e["kind"] == "task_landed"]
        self.assertEqual(len(landed), 1)
        self.assertFalse(landed[0]["hands_free"])
        self.assertEqual(resp.data["tldr"]["hands_free"], 0)

    def test_task_failed_event_with_fingerprint(self):
        task = self.make_task(
            self.board, title="Broke", spec=self.spec, status=TaskStatus.FAILED,
            metadata={
                "last_failure_type": "llm_call_failure",
                "last_failure_reason": "Quota exhausted for claude",
                "failure_class": "quota",
            },
        )
        TaskHistory.objects.create(
            task=task, field_name="status", old_value="EXECUTING", new_value="FAILED",
            changed_by="system@taskit",
        )
        resp = self.client.get(f"/boards/{self.board.id}/story/")
        failed = [e for e in resp.data["events"] if e["kind"] == "task_failed"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["task_id"], task.id)
        self.assertEqual(failed[0]["failure_class"], "quota")
        self.assertIn("Quota", failed[0]["fingerprint"])
        self.assertEqual(resp.data["tldr"]["incidents"], 1)

    def test_merge_conflict_event_needs_human(self):
        task = self.make_task(
            self.board, title="Conflict", spec=self.spec, status=TaskStatus.REVIEW,
            metadata={
                "merge_status": "needs_human",
                "merge_ambiguous_files": ["src/app.py"],
            },
        )
        resp = self.client.get(f"/boards/{self.board.id}/story/")
        conflicts = [e for e in resp.data["events"] if e["kind"] == "merge_conflict"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["task_id"], task.id)
        self.assertIn("human", conflicts[0]["resolution"].lower())
        self.assertEqual(resp.data["tldr"]["waiting_on_human"], 1)
        self.assertEqual(resp.data["tldr"]["incidents"], 1)

    def test_merge_conflict_agent_resolved(self):
        task = self.make_task(
            self.board, title="Auto-merged", spec=self.spec, status=TaskStatus.TESTING,
            metadata={
                "merge_status": "merged",
                "merge_agent_resolved": True,
                "merge_ambiguous_files": ["src/util.py"],
            },
        )
        TaskHistory.objects.create(
            task=task, field_name="status", old_value="REVIEW", new_value="TESTING",
            changed_by="system@taskit",
        )
        resp = self.client.get(f"/boards/{self.board.id}/story/")
        conflicts = [e for e in resp.data["events"] if e["kind"] == "merge_conflict"]
        # merge_status is "merged" now, not "conflict"/"needs_human" → no incident
        self.assertEqual(len(conflicts), 0)

    def test_merge_conflict_from_conflict_status(self):
        task = self.make_task(
            self.board, title="Hard conflict", spec=self.spec, status=TaskStatus.REVIEW,
            metadata={
                "merge_status": "conflict",
                "merge_error": "auto-resolution failed",
            },
        )
        resp = self.client.get(f"/boards/{self.board.id}/story/")
        conflicts = [e for e in resp.data["events"] if e["kind"] == "merge_conflict"]
        self.assertEqual(len(conflicts), 1)
        self.assertIn("auto", conflicts[0]["resolution"].lower())

    def test_reflection_escalation_event(self):
        task = self.make_task(
            self.board, title="Reworked", spec=self.spec, status=TaskStatus.IN_PROGRESS,
        )
        ReflectionReport.objects.create(
            task=task, reviewer_agent="claude", reviewer_model="claude-sonnet-4-5",
            requested_by="system@taskit", status=ReflectionStatus.COMPLETED,
            verdict="NEEDS_WORK", verdict_summary="Missing test coverage for edge case",
            completed_at=timezone.now(),
        )
        resp = self.client.get(f"/boards/{self.board.id}/story/")
        esc = [e for e in resp.data["events"] if e["kind"] == "reflection_escalation"]
        self.assertEqual(len(esc), 1)
        self.assertEqual(esc[0]["task_id"], task.id)
        self.assertEqual(esc[0]["verdict"], "NEEDS_WORK")
        self.assertIn("test coverage", esc[0]["line"].lower())
        self.assertEqual(resp.data["tldr"]["incidents"], 1)

    def test_wave_all_landed_when_spec_complete(self):
        t1 = self.make_task(self.board, title="Task A", spec=self.spec, status=TaskStatus.TESTING)
        t2 = self.make_task(self.board, title="Task B", spec=self.spec, status=TaskStatus.TESTING)
        for t in (t1, t2):
            TaskHistory.objects.create(
                task=t, field_name="status", old_value="TODO", new_value="EXECUTING",
                changed_by="system@taskit",
            )
            TaskHistory.objects.create(
                task=t, field_name="status", old_value="EXECUTING", new_value="TESTING",
                changed_by="system@taskit",
            )
        resp = self.client.get(f"/boards/{self.board.id}/story/")
        waves = [e for e in resp.data["events"] if e["kind"] == "wave_all_landed"]
        self.assertEqual(len(waves), 1)
        self.assertEqual(waves[0]["spec_id"], self.spec.id)
        self.assertIn("all", waves[0]["line"].lower())

    def test_wave_all_landed_not_emitted_when_incomplete(self):
        t1 = self.make_task(self.board, title="Task A", spec=self.spec, status=TaskStatus.TESTING)
        self.make_task(self.board, title="Task B", spec=self.spec, status=TaskStatus.TODO)
        TaskHistory.objects.create(
            task=t1, field_name="status", old_value="TODO", new_value="EXECUTING",
            changed_by="system@taskit",
        )
        TaskHistory.objects.create(
            task=t1, field_name="status", old_value="EXECUTING", new_value="TESTING",
            changed_by="system@taskit",
        )
        resp = self.client.get(f"/boards/{self.board.id}/story/")
        waves = [e for e in resp.data["events"] if e["kind"] == "wave_all_landed"]
        self.assertEqual(len(waves), 0)


class BoardStoryBuilderTests(APITestCase):
    """Prove the CLI diagnostic and the endpoint share one builder."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board)
        self.task = self.make_task(
            self.board, title="Shared", spec=self.spec, status=TaskStatus.TESTING,
        )
        TaskHistory.objects.create(
            task=self.task, field_name="status", old_value="TODO", new_value="EXECUTING",
            changed_by="system@taskit",
        )
        TaskHistory.objects.create(
            task=self.task, field_name="status", old_value="EXECUTING", new_value="TESTING",
            changed_by="system@taskit",
        )

    def test_endpoint_matches_builder(self):
        from tasks.board_story import build_board_story
        from tasks.models import Board

        resp = self.client.get(f"/boards/{self.board.id}/story/")
        direct = build_board_story(Board.objects.get(pk=self.board.id))
        self.assertEqual(resp.data["events"], direct["events"])
        self.assertEqual(resp.data["tldr"], direct["tldr"])


class BoardStoryHtmlTests(APITestCase):
    """The --html output must be a self-contained, non-empty handover page."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board)
        for i in range(5):
            task = self.make_task(
                self.board, title=f"Task {i}", spec=self.spec, status=TaskStatus.TESTING,
            )
            TaskHistory.objects.create(
                task=task, field_name="status", old_value="TODO", new_value="EXECUTING",
                changed_by="system@taskit",
            )
            TaskHistory.objects.create(
                task=task, field_name="status", old_value="EXECUTING", new_value="TESTING",
                changed_by="system@taskit",
            )

    def test_html_renders_non_empty_with_tldr(self):
        from tasks.board_story import build_board_story, render_html
        from tasks.models import Board

        story = build_board_story(Board.objects.get(pk=self.board.id))
        html = render_html(story)
        self.assertGreater(len(html), 500)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("TLDR", html)
        self.assertIn("landed", html.lower())
        self.assertIn(str(story["tldr"]["landed"]), html)
        self.assertNotIn("http://", html.replace("http://www.w3.org", ""))

    def test_html_renders_for_empty_board(self):
        from tasks.board_story import build_board_story, render_html
        from tasks.models import Board

        empty_board = self.make_board(name="Empty")
        story = build_board_story(empty_board)
        html = render_html(story)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("0", html)
