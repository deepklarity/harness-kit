"""Tests for the wave story assembly: GET /specs/<id>/story/.

Covers: endpoint shape, task ordering, dispatch time, redo rounds
(reflection verdicts), merge mode/conflicts, tokens/cost, newest
human-relevant comment, and honest gap reporting when data is missing.

The endpoint (tasks/views.py SpecViewSet.story) and the CLI diagnostic
(testing_tools/spec_trace.py --sections story) share one builder —
tasks/spec_story.py — so these tests are the single source of truth for
both surfaces' behavior.
"""
import json

from .base import APITestCase
from tasks.models import (
    CommentType, ReflectionReport, ReflectionStatus, Task, TaskComment,
    TaskHistory, TaskStatus,
)


def _make_trace_comment(task, input_tokens, output_tokens):
    """Simulate an orchestrator trace comment carrying token usage."""
    content = "\n".join([
        '{"type":"text","part":{"text":"done"}}',
        json.dumps({"modelUsage": {"model": {
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
        }}}),
    ])
    TaskComment.objects.create(
        task=task,
        author_email="odin@system",
        content=content,
        attachments=["trace:execution_jsonl"],
    )


class SpecStoryEndpointTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board)
        self.alice = self.make_user(name="Alice", email="alice@test.com")

    def test_story_not_found(self):
        resp = self.client.get("/specs/999999/story/")
        self.assertEqual(resp.status_code, 404)

    def test_story_shape_and_no_tasks(self):
        resp = self.client.get(f"/specs/{self.spec.id}/story/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["spec_id"], self.spec.id)
        self.assertEqual(resp.data["odin_id"], self.spec.odin_id)
        self.assertEqual(resp.data["task_count"], 0)
        self.assertEqual(resp.data["tasks"], [])

    def test_story_task_order_follows_dependencies_not_creation_order(self):
        # Create the downstream task first, upstream second — the story
        # must still return them in dependency order.
        t2 = self.make_task(self.board, title="Second", spec=self.spec, status=TaskStatus.TODO)
        t1 = self.make_task(self.board, title="First", spec=self.spec, status=TaskStatus.TODO)
        t2.depends_on = [str(t1.id)]
        t2.save(update_fields=["depends_on"])

        resp = self.client.get(f"/specs/{self.spec.id}/story/")
        ids = [t["task_id"] for t in resp.data["tasks"]]
        self.assertEqual(ids, [t1.id, t2.id])

    def test_story_reports_agent_model_status_duration(self):
        task = self.make_task(
            self.board, title="Build thing", spec=self.spec,
            status=TaskStatus.DONE, assignee=self.alice,
            model_name="claude-sonnet-4-5",
            metadata={"last_duration_ms": 45000},
        )
        resp = self.client.get(f"/specs/{self.spec.id}/story/")
        entry = resp.data["tasks"][0]
        self.assertEqual(entry["task_id"], task.id)
        self.assertEqual(entry["title"], "Build thing")
        self.assertEqual(entry["status"], TaskStatus.DONE)
        self.assertEqual(entry["agent"], "Alice")
        self.assertEqual(entry["model"], "claude-sonnet-4-5")
        self.assertEqual(entry["duration_ms"], 45000)

    def test_story_dispatch_time_from_history(self):
        task = self.make_task(self.board, title="Dispatched", spec=self.spec, status=TaskStatus.DONE)
        TaskHistory.objects.create(
            task=task, field_name="status", old_value="TODO", new_value="EXECUTING",
            changed_by="odin@system",
        )
        resp = self.client.get(f"/specs/{self.spec.id}/story/")
        entry = resp.data["tasks"][0]
        self.assertIsNotNone(entry["dispatched_at"])

    def test_story_missing_dispatch_time_is_a_gap_not_a_hidden_row(self):
        self.make_task(self.board, title="Never dispatched", spec=self.spec, status=TaskStatus.TODO)
        resp = self.client.get(f"/specs/{self.spec.id}/story/")
        entry = resp.data["tasks"][0]
        self.assertIsNone(entry["dispatched_at"])
        self.assertTrue(any("dispatch time" in g for g in entry["gaps"]))

    def test_story_tokens_and_cost_from_trace_comment(self):
        task = self.make_task(
            self.board, title="Priced task", spec=self.spec,
            status=TaskStatus.DONE, model_name="claude-sonnet-4-5-20250929",
        )
        _make_trace_comment(task, input_tokens=1000, output_tokens=500)
        resp = self.client.get(f"/specs/{self.spec.id}/story/")
        entry = resp.data["tasks"][0]
        self.assertEqual(entry["tokens"]["input"], 1000)
        self.assertEqual(entry["tokens"]["output"], 500)
        self.assertEqual(entry["tokens"]["total"], 1500)

    def test_story_no_cost_capture_is_reported_as_gap(self):
        task = self.make_task(self.board, title="No trace", spec=self.spec, status=TaskStatus.DONE)
        resp = self.client.get(f"/specs/{self.spec.id}/story/")
        entry = resp.data["tasks"][0]
        self.assertEqual(entry["tokens"]["total"], 0)
        self.assertIsNone(entry["cost_usd"])
        self.assertTrue(any("cost capture" in g for g in entry["gaps"]))

    def test_story_redo_rounds_count_completed_reflections(self):
        task = self.make_task(self.board, title="Reworked", spec=self.spec, status=TaskStatus.DONE)
        ReflectionReport.objects.create(
            task=task, reviewer_agent="claude", reviewer_model="claude-sonnet-4-5",
            requested_by="system@taskit", status=ReflectionStatus.COMPLETED, verdict="NEEDS_WORK",
        )
        ReflectionReport.objects.create(
            task=task, reviewer_agent="claude", reviewer_model="claude-sonnet-4-5",
            requested_by="system@taskit", status=ReflectionStatus.COMPLETED, verdict="PASS",
        )
        resp = self.client.get(f"/specs/{self.spec.id}/story/")
        entry = resp.data["tasks"][0]
        self.assertEqual(entry["redo_rounds"]["count"], 2)
        verdicts = [v["verdict"] for v in entry["redo_rounds"]["verdicts"]]
        self.assertEqual(verdicts, ["NEEDS_WORK", "PASS"])

    def test_story_zero_redo_rounds_when_no_reflections(self):
        self.make_task(self.board, title="Clean pass", spec=self.spec, status=TaskStatus.DONE)
        resp = self.client.get(f"/specs/{self.spec.id}/story/")
        entry = resp.data["tasks"][0]
        self.assertEqual(entry["redo_rounds"]["count"], 0)
        self.assertEqual(entry["redo_rounds"]["verdicts"], [])

    def test_story_merge_mode_and_conflicts_surfaced(self):
        task = self.make_task(
            self.board, title="Conflicted", spec=self.spec, status=TaskStatus.REVIEW,
            metadata={
                "merge_status": "needs_human",
                "merge_ambiguous_files": ["src/app.py"],
                "merge_error": "conflict on src/app.py",
            },
        )
        resp = self.client.get(f"/specs/{self.spec.id}/story/")
        entry = resp.data["tasks"][0]
        self.assertEqual(entry["merge"]["status"], "needs_human")
        self.assertIn("escalated", entry["merge"]["mode"])
        self.assertEqual(entry["merge"]["conflicting_files"], ["src/app.py"])
        self.assertEqual(entry["merge"]["error"], "conflict on src/app.py")

    def test_story_missing_merge_record_on_terminal_task_is_a_gap(self):
        # A DONE task with no merge_status at all (pre-ledger merge) must
        # still appear — the story says the merge data is missing, not
        # hide the row.
        self.make_task(self.board, title="Pre-ledger", spec=self.spec, status=TaskStatus.DONE)
        resp = self.client.get(f"/specs/{self.spec.id}/story/")
        entry = resp.data["tasks"][0]
        self.assertIsNone(entry["merge"])
        self.assertTrue(any("merge record" in g for g in entry["gaps"]))

    def test_story_latest_comment_prefers_reviewer_verdict_over_agent_noise(self):
        task = self.make_task(self.board, title="Reviewed", spec=self.spec, status=TaskStatus.DONE)
        TaskComment.objects.create(
            task=task, author_email="claude+sonnet@odin.agent",
            content="Reviewer verdict: NEEDS_WORK — missing test coverage",
            comment_type=CommentType.REFLECTION,
        )
        TaskComment.objects.create(
            task=task, author_email="claude+sonnet@odin.agent",
            content="Working on it now",
            comment_type=CommentType.STATUS_UPDATE,
        )
        resp = self.client.get(f"/specs/{self.spec.id}/story/")
        entry = resp.data["tasks"][0]
        self.assertIn("Reviewer verdict", entry["latest_comment"]["headline"])
        self.assertEqual(entry["latest_comment"]["comment_type"], CommentType.REFLECTION)

    def test_story_latest_comment_picks_operator_note_over_agent_status_update(self):
        task = self.make_task(self.board, title="Operator note", spec=self.spec, status=TaskStatus.DONE)
        TaskComment.objects.create(
            task=task, author_email="claude+sonnet@odin.agent",
            content="Routine status update",
            comment_type=CommentType.STATUS_UPDATE,
        )
        TaskComment.objects.create(
            task=task, author_email="alice@test.com",
            content="Approved manually after spot-check",
            comment_type=CommentType.STATUS_UPDATE,
        )
        resp = self.client.get(f"/specs/{self.spec.id}/story/")
        entry = resp.data["tasks"][0]
        self.assertIn("Approved manually", entry["latest_comment"]["headline"])

    def test_story_no_comments_is_a_gap(self):
        self.make_task(self.board, title="Silent task", spec=self.spec, status=TaskStatus.TODO)
        resp = self.client.get(f"/specs/{self.spec.id}/story/")
        entry = resp.data["tasks"][0]
        self.assertIsNone(entry["latest_comment"])
        self.assertTrue(any("no comments" in g for g in entry["gaps"]))


class SpecTraceShareCodeTests(APITestCase):
    """Prove the CLI diagnostic and the endpoint share one builder."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board)
        self.task = self.make_task(self.board, title="Shared", spec=self.spec, status=TaskStatus.DONE)

    def test_spec_trace_story_section_matches_endpoint(self):
        from tasks.spec_story import build_spec_story

        resp = self.client.get(f"/specs/{self.spec.id}/story/")
        from tasks.models import Spec
        direct = build_spec_story(Spec.objects.get(pk=self.spec.id))
        self.assertEqual(resp.data["tasks"], direct["tasks"])

    def test_spec_trace_topological_sort_reused_by_spec_story(self):
        import testing_tools.spec_trace as spec_trace
        from tasks.spec_story import topological_sort

        self.assertIs(spec_trace.topological_sort, topological_sort)
