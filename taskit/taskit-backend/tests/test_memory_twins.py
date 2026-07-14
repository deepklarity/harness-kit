"""Tests for the Memory "twins" feature: task-similarity scoring (tasks/similarity.py).

Scenario matrix:
  - known-similar finished task ranks above an unrelated finished task
  - scoping: only same-board, only finished (DONE/FAILED/CANCELED) tasks are candidates
  - structural signal: same spec boosts a candidate's score
  - structural signal: overlapping files-touched (from diff_stat) boosts score
  - empty history degrades silently (no twins, no crash)
  - dispatch (poll_and_execute) posts exactly one twins comment, and is idempotent
    per dispatch (does not double-post on a repeated call for the same run_token)
  - dispatch with no history posts no comment at all
  - the task-detail API exposes twins with id/title/outcome/tokens/duration/redo/agent
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import MagicMock, patch

from tasks.dag_executor import poll_and_execute
from tasks.models import CommentType, Task, TaskComment, TaskStatus
from tasks.similarity import find_twins, post_twins_comment
from tests.base import APITestCase


def _make_trace_comment(task, input_tokens=38, output_tokens=925):
    """Attach a fake execution trace so compute_usage_from_trace() has data."""
    raw_output = "\n".join([
        '{"type":"text","part":{"text":"-------ODIN-STATUS-------\\nSUCCESS\\n-------ODIN-SUMMARY-------\\nDone."}}',
        (
            '{"modelUsage":{"claude-sonnet-4-5":{"inputTokens":%d,"outputTokens":%d}}}'
            % (input_tokens, output_tokens)
        ),
    ])
    TaskComment.objects.create(
        task=task,
        author_email="odin@system",
        content=raw_output,
        attachments=["trace:execution_jsonl"],
    )


class FindTwinsScoringTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.agent = self.make_user(name="Claude", email="claude@odin.agent")

    def test_ranks_similar_finished_task_above_unrelated(self):
        similar = self.make_task(
            self.board,
            title="Fix login redirect bug on Safari",
            description="Users on Safari get redirected to /404 after login.",
            status=TaskStatus.DONE,
        )
        unrelated = self.make_task(
            self.board,
            title="Add quarterly revenue export to CSV",
            description="Finance wants a CSV export of quarterly revenue by region.",
            status=TaskStatus.DONE,
        )
        query = self.make_task(
            self.board,
            title="Fix login redirect bug on Firefox",
            description="Users on Firefox get redirected to /404 after login too.",
            status=TaskStatus.IN_PROGRESS,
        )

        twins = find_twins(query)

        self.assertGreaterEqual(len(twins), 1)
        self.assertEqual(twins[0]["task_id"], similar.id)
        similar_ids = [t["task_id"] for t in twins]
        if unrelated.id in similar_ids:
            self.assertLess(
                [t["score"] for t in twins if t["task_id"] == unrelated.id][0],
                twins[0]["score"],
            )

    def test_scoped_to_same_board_only(self):
        other_board = self.make_board(name="Other Board")
        self.make_task(
            other_board,
            title="Fix login redirect bug on Safari",
            description="Users on Safari get redirected to /404 after login.",
            status=TaskStatus.DONE,
        )
        query = self.make_task(
            self.board,
            title="Fix login redirect bug on Firefox",
            description="Users on Firefox get redirected to /404 after login too.",
            status=TaskStatus.IN_PROGRESS,
        )

        self.assertEqual(find_twins(query), [])

    def test_only_finished_statuses_are_candidates(self):
        self.make_task(
            self.board,
            title="Fix login redirect bug on Safari",
            description="Users on Safari get redirected to /404 after login.",
            status=TaskStatus.IN_PROGRESS,
        )
        query = self.make_task(
            self.board,
            title="Fix login redirect bug on Firefox",
            description="Users on Firefox get redirected to /404 after login too.",
            status=TaskStatus.TODO,
        )

        self.assertEqual(find_twins(query), [])

    def test_failed_tasks_are_still_twins(self):
        """'Finished relatives' includes failures — how it went is signal too."""
        failed = self.make_task(
            self.board,
            title="Fix login redirect bug on Safari",
            description="Users on Safari get redirected to /404 after login.",
            status=TaskStatus.FAILED,
        )
        query = self.make_task(
            self.board,
            title="Fix login redirect bug on Firefox",
            description="Users on Firefox get redirected to /404 after login too.",
            status=TaskStatus.IN_PROGRESS,
        )

        twins = find_twins(query)
        self.assertEqual(twins[0]["task_id"], failed.id)
        self.assertEqual(twins[0]["outcome"], TaskStatus.FAILED)

    def test_same_spec_boosts_score_over_generic_match(self):
        spec = self.make_spec(self.board, odin_id="sp_042", title="Auth revamp")
        same_spec_twin = self.make_task(
            self.board,
            title="Update settings page copy",
            description="Minor copy tweak on the settings page.",
            status=TaskStatus.DONE,
            spec=spec,
        )
        other_spec_twin = self.make_task(
            self.board,
            title="Update settings page copy",
            description="Minor copy tweak on the settings page.",
            status=TaskStatus.DONE,
        )
        query = self.make_task(
            self.board,
            title="Update settings page copy",
            description="Minor copy tweak on the settings page.",
            status=TaskStatus.IN_PROGRESS,
            spec=spec,
        )

        twins = find_twins(query)
        scores = {t["task_id"]: t["score"] for t in twins}
        self.assertGreater(scores[same_spec_twin.id], scores[other_spec_twin.id])

    def test_files_touched_overlap_boosts_score(self):
        overlapping = self.make_task(
            self.board,
            title="Tweak pricing page",
            description="Small pricing page adjustment.",
            status=TaskStatus.DONE,
            metadata={"diff_stat": " src/pricing.py | 12 ++++++------\n src/utils.py | 2 +-\n"},
        )
        non_overlapping = self.make_task(
            self.board,
            title="Tweak pricing page",
            description="Small pricing page adjustment.",
            status=TaskStatus.DONE,
            metadata={"diff_stat": " src/unrelated_module.py | 4 ++--\n"},
        )
        query = self.make_task(
            self.board,
            title="Tweak pricing page",
            description="Small pricing page adjustment.",
            status=TaskStatus.IN_PROGRESS,
            metadata={"diff_stat": " src/pricing.py | 3 ++-\n"},
        )

        twins = find_twins(query)
        scores = {t["task_id"]: t["score"] for t in twins}
        self.assertGreater(scores[overlapping.id], scores[non_overlapping.id])

    def test_empty_history_degrades_silently(self):
        query = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        self.assertEqual(find_twins(query), [])

    def test_serialized_fields_present(self):
        twin_task = self.make_task(
            self.board,
            title="Fix login redirect bug on Safari",
            description="Users on Safari get redirected to /404 after login.",
            status=TaskStatus.DONE,
            assignee=self.agent,
            metadata={"last_duration_ms": 42000.0, "rework_count": 2},
        )
        _make_trace_comment(twin_task, input_tokens=38, output_tokens=925)
        query = self.make_task(
            self.board,
            title="Fix login redirect bug on Firefox",
            description="Users on Firefox get redirected to /404 after login too.",
            status=TaskStatus.IN_PROGRESS,
        )

        twins = find_twins(query)
        twin = twins[0]
        self.assertEqual(twin["task_id"], twin_task.id)
        self.assertEqual(twin["title"], twin_task.title)
        self.assertEqual(twin["outcome"], TaskStatus.DONE)
        self.assertEqual(twin["tokens"], 963)
        self.assertEqual(twin["duration_ms"], 42000.0)
        self.assertEqual(twin["redo_rounds"], 2)
        self.assertEqual(twin["agent"], "Claude")
        self.assertEqual(twin["proof_path"], f".proof/task-{twin_task.id}/proof.md")

    def test_serialized_includes_model_from_model_name(self):
        twin_task = self.make_task(
            self.board,
            title="Fix login redirect bug on Safari",
            description="Users on Safari get redirected to /404 after login.",
            status=TaskStatus.DONE,
            assignee=self.agent,
            model_name="claude-sonnet-4-5",
        )
        _make_trace_comment(twin_task, input_tokens=1, output_tokens=2)
        query = self.make_task(
            self.board,
            title="Fix login redirect bug on Firefox",
            description="Users on Firefox get redirected to /404 after login too.",
            status=TaskStatus.IN_PROGRESS,
        )

        twin = find_twins(query)[0]
        self.assertEqual(twin["model"], "claude-sonnet-4-5")

    def test_serialized_model_none_when_unset(self):
        twin_task = self.make_task(
            self.board,
            title="Fix login redirect bug on Safari",
            description="Users on Safari get redirected to /404 after login.",
            status=TaskStatus.DONE,
        )
        _make_trace_comment(twin_task, input_tokens=1, output_tokens=2)
        query = self.make_task(
            self.board,
            title="Fix login redirect bug on Firefox",
            description="Users on Firefox get redirected to /404 after login too.",
            status=TaskStatus.IN_PROGRESS,
        )

        twin = find_twins(query)[0]
        self.assertIsNone(twin["model"])


class PostTwinsCommentDispatchTests(APITestCase):
    """Integration: the executor's dispatch path posts one twins comment."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(allow_project_root_execution=True)
        self.user = self.make_user()

    @patch("tasks.dag_executor.execute_single_task")
    def test_dispatch_posts_exactly_one_twins_comment(self, mock_exec):
        mock_exec.delay.return_value = MagicMock(id="fake-celery-twins-1")
        twin_task = self.make_task(
            self.board,
            title="Fix login redirect bug on Safari",
            description="Users on Safari get redirected to /404 after login.",
            status=TaskStatus.DONE,
            assignee=self.user,
            metadata={"last_duration_ms": 15000.0, "rework_count": 1},
        )
        _make_trace_comment(twin_task)

        task = self.make_task(
            self.board,
            title="Fix login redirect bug on Firefox",
            description="Users on Firefox get redirected to /404 after login too.",
            status=TaskStatus.IN_PROGRESS,
            assignee=self.user,
            depends_on=[],
        )

        poll_and_execute()

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.EXECUTING)
        twins_comments = TaskComment.objects.filter(
            task=task, author_label="memory",
        )
        self.assertEqual(twins_comments.count(), 1)
        comment = twins_comments.first()
        self.assertEqual(comment.comment_type, CommentType.STATUS_UPDATE)
        self.assertIn(str(twin_task.id), comment.content)
        self.assertIn(twin_task.title, comment.content)
        self.assertIn(f".proof/task-{twin_task.id}/proof.md", comment.content)

    @patch("tasks.dag_executor.execute_single_task")
    def test_dispatch_with_no_history_posts_no_twins_comment(self, mock_exec):
        mock_exec.delay.return_value = MagicMock(id="fake-celery-twins-2")
        task = self.make_task(
            self.board,
            status=TaskStatus.IN_PROGRESS,
            assignee=self.user,
            depends_on=[],
        )

        poll_and_execute()

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.EXECUTING)
        self.assertEqual(
            TaskComment.objects.filter(task=task, author_label="memory").count(), 0,
        )

    def test_post_twins_comment_idempotent_per_run_token(self):
        """A second call for the same dispatch (same run_token) must not double-post."""
        twin_task = self.make_task(
            self.board,
            title="Fix login redirect bug on Safari",
            description="Users on Safari get redirected to /404 after login.",
            status=TaskStatus.DONE,
        )
        task = self.make_task(
            self.board,
            title="Fix login redirect bug on Firefox",
            description="Users on Firefox get redirected to /404 after login too.",
            status=TaskStatus.EXECUTING,
            metadata={"active_execution": {"strategy": "celery_dag", "run_token": "tok-1"}},
        )

        first = post_twins_comment(task)
        second = post_twins_comment(task)

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(
            TaskComment.objects.filter(task=task, author_label="memory").count(), 1,
        )


class TaskDetailTwinsApiTests(APITestCase):
    """The task-detail API exposes twins directly."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.agent = self.make_user(name="Claude", email="claude@odin.agent")

    def test_detail_endpoint_returns_twins(self):
        twin_task = self.make_task(
            self.board,
            title="Fix login redirect bug on Safari",
            description="Users on Safari get redirected to /404 after login.",
            status=TaskStatus.DONE,
            assignee=self.agent,
            metadata={"last_duration_ms": 8000.0, "rework_count": 0},
        )
        _make_trace_comment(twin_task, input_tokens=10, output_tokens=20)
        task = self.make_task(
            self.board,
            title="Fix login redirect bug on Firefox",
            description="Users on Firefox get redirected to /404 after login too.",
            status=TaskStatus.IN_PROGRESS,
        )

        resp = self.client.get(f"/tasks/{task.id}/detail/")

        self.assertEqual(resp.status_code, 200)
        twins = resp.data.get("twins")
        self.assertIsNotNone(twins)
        self.assertEqual(len(twins), 1)
        twin = twins[0]
        self.assertEqual(twin["task_id"], twin_task.id)
        self.assertEqual(twin["title"], twin_task.title)
        self.assertEqual(twin["outcome"], TaskStatus.DONE)
        self.assertEqual(twin["tokens"], 30)
        self.assertEqual(twin["duration_ms"], 8000.0)
        self.assertEqual(twin["redo_rounds"], 0)
        self.assertEqual(twin["agent"], "Claude")

    def test_detail_endpoint_twins_empty_when_no_history(self):
        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        resp = self.client.get(f"/tasks/{task.id}/detail/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.get("twins"), [])


class TaskDetailEstimateActualApiTests(APITestCase):
    """The task-detail API exposes the estimate (quote) and actual cost as
    structured fields, not as comment markdown to be parsed."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def test_detail_returns_estimate_from_metadata(self):
        estimate = {
            "confidence": "high",
            "twin_count": 3,
            "tokens_median": 2500000,
            "duration_ms_median": 900000,
            "source_twin_ids": [10, 11, 12],
        }
        task = self.make_task(
            self.board,
            status=TaskStatus.IN_PROGRESS,
            metadata={"estimate": estimate},
        )
        resp = self.client.get(f"/tasks/{task.id}/detail/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["estimate"], estimate)

    def test_detail_returns_actual_from_metadata(self):
        actual = {"tokens": 1800000, "duration_ms": 700000, "transition": "DONE"}
        task = self.make_task(
            self.board,
            status=TaskStatus.DONE,
            metadata={
                "estimate": {"confidence": "medium", "twin_count": 2},
                "actual": actual,
            },
        )
        resp = self.client.get(f"/tasks/{task.id}/detail/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["actual"], actual)

    def test_detail_estimate_and_actual_none_when_absent(self):
        task = self.make_task(self.board, status=TaskStatus.IN_PROGRESS)
        resp = self.client.get(f"/tasks/{task.id}/detail/")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.data["estimate"])
        self.assertIsNone(resp.data["actual"])
