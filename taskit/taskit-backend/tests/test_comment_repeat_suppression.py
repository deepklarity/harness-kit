"""Fable task #360 — system-comment repeat suppression + double-post fix.

The task page's comment stream gets buried under byte-identical system
messages on retried tasks: the "Memory — closest finished twins" card
posted N times (once per retry, unchanged), the 8KB "Effective input"
dump N times, and the failure burst landing twice in seconds when a
requeue races the runner wrapper.

These tests pin the contract:
  * a content-aware dedup helper guards every system-comment posting site
  * a retried task gets each system message ONCE per real change
  * the runner wrapper never re-posts a failure burst a requeue already
    produced (the run_token supersession fence)

Failing-first: every test fails before the implementation lands.
"""
import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import patch

from tests.base import APITestCase
from tasks import task_runs
from tasks.dag_executor import execute_single_task
from tasks.failure_policy import post_failure_history_comment
from tasks.mistakes import record_execution_mistake
from tasks.models import TaskComment, TaskStatus
from tasks.similarity import post_twins_comment


# ── 1. Dedup helper ──────────────────────────────────────────────────


class CommentDedupHelperTests(APITestCase):
    """The single chokepoint every system-comment poster asks before writing:
    'does a comment with this exact body already exist on this task?'"""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board)

    def test_returns_true_when_identical_body_exists(self):
        from tasks.comment_dedup import has_identical_comment

        TaskComment.objects.create(
            task=self.task, author_email="odin+memory@system",
            author_label="memory", content="twins card body",
        )
        self.assertTrue(has_identical_comment(self.task, "twins card body"))

    def test_returns_false_when_no_match(self):
        from tasks.comment_dedup import has_identical_comment

        TaskComment.objects.create(
            task=self.task, author_email="odin+memory@system",
            author_label="memory", content="twins card body",
        )
        self.assertFalse(has_identical_comment(self.task, "different body"))

    def test_scopes_by_author_label(self):
        """A twins card (author_label='memory') never collides with a
        failure-history card — the dedup is per system-message kind."""
        from tasks.comment_dedup import has_identical_comment

        TaskComment.objects.create(
            task=self.task, author_email="odin+dag-executor@system",
            author_label="odin-dag-executor", content="shared body text",
        )
        # Same body, different kind → not a duplicate.
        self.assertFalse(
            has_identical_comment(self.task, "shared body text", author_label="memory")
        )
        # Same body, same kind → duplicate.
        self.assertTrue(
            has_identical_comment(
                self.task, "shared body text", author_label="odin-dag-executor"
            )
        )


# ── 2. Twins repeat suppression (the 11x bug) ───────────────────────


class TwinsRepeatSuppressionTests(APITestCase):
    """A task retried N times must post the twins card ONCE while the board
    history is unchanged — not once per attempt. It re-posts only when the
    twin set actually changes (a new finished relative, a different assignee)."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.twin = self.make_task(
            self.board,
            title="Fix login redirect bug on Safari",
            description="Users on Safari get redirected to /404 after login.",
            status=TaskStatus.DONE,
        )

    def _retry_dispatch(self, task, run_token):
        """Simulate a fresh dispatch: a new run_token, then the dispatch-time
        twins post."""
        md = dict(task.metadata or {})
        md["active_execution"] = {"strategy": "celery_dag", "run_token": run_token}
        task.metadata = md
        task.save(update_fields=["metadata"])
        return post_twins_comment(task)

    def test_first_dispatch_posts_twins(self):
        task = self.make_task(
            self.board,
            title="Fix login redirect bug on Firefox",
            description="Users on Firefox get redirected to /404 after login too.",
            status=TaskStatus.EXECUTING,
        )
        self._retry_dispatch(task, "tok-1")
        self.assertEqual(
            TaskComment.objects.filter(task=task, author_label="memory").count(), 1
        )

    def test_retried_task_posts_twins_once_when_unchanged(self):
        """Three retries, same board history → ONE twins card (was three)."""
        task = self.make_task(
            self.board,
            title="Fix login redirect bug on Firefox",
            description="Users on Firefox get redirected to /404 after login too.",
            status=TaskStatus.EXECUTING,
        )
        self._retry_dispatch(task, "tok-1")
        task.refresh_from_db()
        self._retry_dispatch(task, "tok-2")
        task.refresh_from_db()
        self._retry_dispatch(task, "tok-3")

        count = TaskComment.objects.filter(task=task, author_label="memory").count()
        self.assertEqual(count, 1)

    def test_reposts_when_twin_set_changes(self):
        """A new finished relative changes the twin card body → re-post."""
        task = self.make_task(
            self.board,
            title="Fix login redirect bug on Firefox",
            description="Users on Firefox get redirected to /404 after login too.",
            status=TaskStatus.EXECUTING,
        )
        self._retry_dispatch(task, "tok-1")
        self.assertEqual(
            TaskComment.objects.filter(task=task, author_label="memory").count(), 1
        )

        # A second finished relative lands between retries → the twin set
        # (and thus the card body) changes.
        self.make_task(
            self.board,
            title="Fix login redirect bug on Chrome",
            description="Users on Chrome get redirected to /404 after login too.",
            status=TaskStatus.DONE,
        )
        task.refresh_from_db()
        self._retry_dispatch(task, "tok-2")

        self.assertEqual(
            TaskComment.objects.filter(task=task, author_label="memory").count(), 2
        )


# ── 3. Failure-history (fingerprint) repeat suppression ────────────


class FailureHistoryRepeatSuppressionTests(APITestCase):
    """post_failure_history_comment must not re-post an identical history
    card when the same failure is processed twice (the double-post)."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def _failing_task_with_prior(self):
        prior = self.make_task(
            self.board,
            title="Fix login redirect bug on Safari",
            description="Users on Safari get redirected to /404 after login.",
            status=TaskStatus.FAILED,
            metadata={
                "last_failure_type": "agent_execution_failure",
                "last_failure_reason": "Output truncated, no status emitted.",
                "failure_class": "truncation",
            },
        )
        record_execution_mistake(prior, run_token="prior-tok")

        current = self.make_task(
            self.board,
            title="Fix login redirect bug on Firefox",
            description="Users on Firefox get redirected to /404 after login too.",
            status=TaskStatus.FAILED,
            metadata={
                "last_failure_type": "agent_execution_failure",
                "last_failure_reason": "Output truncated, no status emitted.",
                "failure_class": "truncation",
            },
        )
        return current

    def test_identical_failure_history_posted_once(self):
        task = self._failing_task_with_prior()

        post_failure_history_comment(task)
        post_failure_history_comment(task)

        count = TaskComment.objects.filter(
            task=task, content__startswith="Failure history:"
        ).count()
        self.assertEqual(count, 1)


# ── 4. Execution-result failure comment dedup (odin POST retry) ────


class ExecutionResultCommentDedupTests(APITestCase):
    """If odin retries the execution_result POST (transient network error),
    the second identical result must not post a second 'Failed/Completed in
    Xs' comment."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board, status=TaskStatus.EXECUTING)

    def _post_result(self):
        return self.client.post(
            f"/tasks/{self.task.id}/execution_result/",
            {
                "execution_result": {
                    "success": True,
                    "raw_output": "done",
                    "duration_ms": 100.0,
                    "agent": "claude",
                    "metadata": {},
                },
                "status": "REVIEW",
                "updated_by": "claude+claude-sonnet-4-5@odin.agent",
            },
            format="json",
        )

    def test_duplicate_execution_result_posts_comment_once(self):
        self._post_result()
        self.task.refresh_from_db()
        # A real retry: odin POSTs the same result again.
        self._post_result()

        count = TaskComment.objects.filter(
            task=self.task, author_email="claude+claude-sonnet-4-5@odin.agent"
        ).count()
        self.assertEqual(count, 1)


# ── 5. Runner wrapper supersession fence (the requeue double-post) ──


class RunnerSupersessionFenceTests(APITestCase):
    """The requeue double-post: odin records FAILED + the failure policy
    requeues (new run B) BEFORE the original runner wrapper (run A) checks
    the subprocess exit code. The wrapper sees status=EXECUTING again and
    re-posts run A's failure burst. The fence: a wrapper only posts for its
    OWN run — if the current running run is a different token, the run was
    superseded and the wrapper must not post."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(allow_project_root_execution=True)
        self.user = self.make_user()

    def test_runner_skips_failure_post_when_run_superseded(self):
        task = self.make_task(
            self.board,
            status=TaskStatus.EXECUTING,
            assignee=self.user,
            metadata={"active_execution": {"strategy": "celery_dag", "run_token": "run-A"}},
        )
        task_runs.start_run(task, "run-A")

        # Simulate what odin + the requeue did WHILE the wrapper's subprocess
        # was finishing: run A completed, the failure policy requeued under a
        # brand-new run B, and the task is EXECUTING again under B.
        def _odin_requeued(task_id, *args, **kwargs):
            t = type(task).objects.get(id=task_id)
            task_runs.finish_run("run-A")
            task_runs.start_run(t, "run-B")
            md = dict(t.metadata or {})
            md["active_execution"] = {"strategy": "celery_dag", "run_token": "run-B"}
            t.metadata = md
            t.status = TaskStatus.EXECUTING
            t.save(update_fields=["status", "metadata"])
            return (1, "execution")  # nonzero exit

        with patch(
            "tasks.dag_executor._run_subprocess_with_cancellation",
            side_effect=_odin_requeued,
        ):
            execute_single_task(task.id, run_token="run-A")

        # The wrapper must NOT post its own failure burst — the requeue (run B)
        # already owns the task. No 'Origin: taskit_dag_executor' comment.
        wrapper = TaskComment.objects.filter(
            task=task,
            author_email="odin+dag-executor@system",
            content__contains="Origin: taskit_dag_executor",
        )
        self.assertEqual(
            wrapper.count(), 0, "wrapper re-posted a failure burst a requeue already owned"
        )
