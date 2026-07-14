"""Auto-redispatch of infra-class failures (task #137).

Origin: every infra failure today (stale_execution recovery kills,
disk-full silent deaths, provider truncation) required the OPERATOR to
notice and PATCH the task back to IN_PROGRESS by hand.  The failure
taxonomy already distinguishes these from real work failures — the
system must retry them itself.

Coverage (acceptance criteria from task #137):

  InfraFailuresAutoRedispatched:
    I1. test_stale_execution_failure_auto_redispatches_to_in_progress
        stale_execution → FAILED → auto-redispatched to IN_PROGRESS,
        assignee + model preserved (continuity).
    I2. test_agent_execution_failure_no_odin_status_auto_redispatches
        agent_execution_failure with the "did not emit ODIN-STATUS"
        signature → FAILED → auto-redispatched to IN_PROGRESS.
    I3. test_agent_execution_failure_truncated_signature_auto_redispatches
        agent_execution_failure with "truncated" signature → auto-redispatched.
    I4. test_agent_execution_failure_terminated_silently_auto_redispatches
        agent_execution_failure with "terminated silently" signature →
        auto-redispatched.
    I5. test_auto_redispatch_records_counter_and_history_in_metadata
        metadata.auto_redispatch_count == 1 after first retry,
        metadata.auto_redispatch_history has one entry naming the class.
    I6. test_auto_redispatch_writes_status_update_naming_class_and_attempt
        the auto-redispatch comment names the failure class + attempt number.
    I7. test_auto_redispatch_reuses_f45_continuity_record
        F45 continuity helper was called: assignee + model history rows
        written by system@taskit, last_rework_reason stamped.

  CapRespected:
    C1. test_auto_redispatch_cap_two_retries_max
        After 2 auto-retries (counter == 2), a 3rd infra failure
        is NOT auto-redispatched; task stays FAILED with
        auto_redispatch_cap_reached flag set.
    C2. test_auto_redispatch_cap_reached_idempotent
        Calling _maybe_auto_redispatch_infra_failure repeatedly on a
        cap-reached task does not post duplicate cap-reached comments.

  RealFailuresUntouched:
    R1. test_agent_reported_failure_in_odin_status_not_retried
        Failure reason = "Agent explicitly reported FAILED in ODIN-STATUS
        block." → NOT auto-retried (real work failure).
    R2. test_backend_auth_failure_not_retried
        last_failure_type=backend_auth_failure → NOT auto-retried.
    R3. test_timeout_failure_not_retried
        last_failure_type=timeout → NOT auto-retried.
    R4. test_missing_worktree_failure_not_retried
        last_failure_type=missing_worktree → NOT auto-retried.
    R5. test_agent_execution_failure_other_reason_not_retried
        agent_execution_failure with a "real" reason (e.g.
        "ImportError: cannot import foo") → NOT auto-retried.

  NonVisual — no browser/screenshots needed.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import MagicMock, patch
import time

from tests.base import APITestCase
from tasks.dag_executor import (
    INFRA_AUTO_REDISPATCH_CAP,
    INFRA_AUTO_REDISPATCH_HISTORY_KEY,
    INFRA_AUTO_REDISPATCH_META_KEY,
    _is_infra_failure,
    _maybe_auto_redispatch_infra_failure,
    _recover_stale_executions,
    execute_single_task,
)
from tasks.models import (
    CommentType,
    Task,
    TaskComment,
    TaskHistory,
    TaskStatus,
)


class InfraFailuresAutoRedispatched(APITestCase):
    """I1–I7: infra-class failures auto-redispatch with continuity.

    The headline mandate of task #137: when a FAILED task's
    last_failure_type is `stale_execution` or `agent_execution_failure`
    matching the "did not emit ODIN-STATUS / truncated / terminated
    silently" signature, the dag_executor flips FAILED → IN_PROGRESS,
    preserves assignee + model, increments the metadata counter, and
    fires the execution strategy.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board(allow_project_root_execution=True)
        self.assignee = self.make_user(
            name="claude",
            email="claude@odin.agent",
        )

    # ── I1 ─────────────────────────────────────────────────────────────

    @patch("tasks.dag_executor.execute_single_task")
    def test_stale_execution_failure_auto_redispatches_to_in_progress(self, mock_exec):
        """stale_execution → FAILED → auto-redispatched to IN_PROGRESS, continuity.

        Drives _recover_stale_executions (which is what produces the
        stale_execution classification in production) and verifies the
        auto-redispatch hook fires the EXECUTING transition back to
        IN_PROGRESS without touching assignee + model.
        """
        mock_exec.delay.return_value = MagicMock(id="fake-celery-stale")

        # Task is mid-execution; worker died (pid recorded but no
        # longer alive).  The recovery routine will mark it FAILED with
        # last_failure_type=stale_execution and the hook must then
        # auto-redispatch it.
        task = self.make_task(
            self.board,
            title="Stale worker death",
            status=TaskStatus.EXECUTING,
            assignee=self.assignee,
            model_name="claude-sonnet-4-5",
            metadata={
                "active_execution": {
                    "strategy": "celery_dag",
                    "run_token": "tok-stale",
                    "pid": 99999,           # recorded PID — alive check fails
                    "queued_at": time.time(),
                    "started_at": time.time(),
                },
            },
        )
        original_assignee_id = task.assignee_id
        original_model = task.model_name

        with patch("tasks.dag_executor._pid_is_alive", return_value=False):
            _recover_stale_executions()

        task.refresh_from_db()

        # The recovery routine marked it FAILED with stale_execution.
        # The auto-redispatch hook then flipped it back to IN_PROGRESS.
        self.assertEqual(
            task.status, TaskStatus.IN_PROGRESS,
            "stale_execution must auto-redispatch FAILED → IN_PROGRESS",
        )
        # Continuity preserved.
        self.assertEqual(task.assignee_id, original_assignee_id)
        self.assertEqual(task.model_name, original_model)
        # Counter incremented.
        self.assertEqual(task.metadata.get(INFRA_AUTO_REDISPATCH_META_KEY), 1)
        # Audit trail names the failure class.
        history = task.metadata.get(INFRA_AUTO_REDISPATCH_HISTORY_KEY) or []
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].get("class"), "stale_execution")

    # ── I2 ─────────────────────────────────────────────────────────────

    @patch("tasks.dag_executor._run_subprocess_with_cancellation")
    @patch("tasks.dag_executor._read_log_tail")
    def test_agent_execution_failure_no_odin_status_auto_redispatches(self, mock_tail, mock_run):
        """agent_execution_failure "did not emit ODIN-STATUS" → auto-redispatched.

        The agent process exited non-zero without ever emitting the
        ODIN-STATUS block — provider truncation / network drop / output
        cap.  This is the prototypical infra-class failure the system
        retries itself.
        """
        mock_run.return_value = (1, "odin_non_zero_exit")
        mock_tail.return_value = (
            "Agent did not emit an ODIN-STATUS block. Likely the model "
            "truncated mid-generation or the response terminated silently "
            "(e.g. provider hit output cap, network drop, or unknown error)."
        )

        task = self.make_task(
            self.board,
            title="No ODIN-STATUS case",
            status=TaskStatus.EXECUTING,
            assignee=self.assignee,
            model_name="claude-sonnet-4-5",
        )

        execute_single_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        self.assertEqual(task.metadata.get("last_failure_type"), "agent_execution_failure")
        self.assertEqual(task.metadata.get(INFRA_AUTO_REDISPATCH_META_KEY), 1)

    # ── I3 ─────────────────────────────────────────────────────────────

    @patch("tasks.dag_executor._run_subprocess_with_cancellation")
    @patch("tasks.dag_executor._read_log_tail")
    def test_agent_execution_failure_truncated_signature_auto_redispatches(
        self, mock_tail, mock_run,
    ):
        """agent_execution_failure with 'truncated' in reason → auto-redispatched.

        Truncation is the canonical infra failure mode: provider cut the
        response mid-stream.  Even when the surrounding reason text is
        not the literal "did not emit ODIN-STATUS" line, the
        'truncated mid-generation' / 'response truncated' signature
        flags it.
        """
        mock_run.return_value = (1, "odin_non_zero_exit")
        mock_tail.return_value = (
            "Response truncated at 8192 tokens (output cap hit)."
        )

        task = self.make_task(
            self.board,
            title="Truncated response",
            status=TaskStatus.EXECUTING,
            assignee=self.assignee,
            model_name="claude-sonnet-4-5",
        )

        execute_single_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        self.assertEqual(task.metadata.get(INFRA_AUTO_REDISPATCH_META_KEY), 1)

    # ── I4 ─────────────────────────────────────────────────────────────

    @patch("tasks.dag_executor._run_subprocess_with_cancellation")
    @patch("tasks.dag_executor._read_log_tail")
    def test_agent_execution_failure_terminated_silently_auto_redispatches(
        self, mock_tail, mock_run,
    ):
        """agent_execution_failure with 'terminated silently' → auto-redispatched.

        The process was killed without producing any output.  Not a real
        failure — the agent never had a chance to report anything.
        """
        mock_run.return_value = (1, "odin_non_zero_exit")
        mock_tail.return_value = (
            "Agent produced no output; likely the CLI crashed or the model "
            "terminated silently before emitting anything."
        )

        task = self.make_task(
            self.board,
            title="Terminated silently",
            status=TaskStatus.EXECUTING,
            assignee=self.assignee,
            model_name="claude-sonnet-4-5",
        )

        execute_single_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        self.assertEqual(task.metadata.get(INFRA_AUTO_REDISPATCH_META_KEY), 1)

    # ── I4b (task #339) ───────────────────────────────────────────────

    @patch("tasks.dag_executor._run_subprocess_with_cancellation")
    @patch("tasks.dag_executor._read_log_tail")
    def test_backend_unreachable_exit_auto_redispatches(self, mock_tail, mock_run):
        """A 'backend unreachable' exit is transport-class infra — auto-redispatched.

        Task #339: a transient backend blip during task ID resolution
        must not mark the task crashed (which would blame the agent and
        hold for a human). It must classify as transport_error and retry
        with the same assignee.
        """
        mock_run.return_value = (1, "odin_non_zero_exit")
        mock_tail.return_value = (
            "Could not reach task backend after 3 attempts. "
            "Backend unreachable: the backend may be restarting"
        )

        task = self.make_task(
            self.board,
            title="Backend unreachable",
            status=TaskStatus.EXECUTING,
            assignee=self.assignee,
            model_name="claude-sonnet-4-5",
        )

        execute_single_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        self.assertEqual(
            task.metadata.get("failure_class"), "transport_error",
        )
        self.assertEqual(task.metadata.get(INFRA_AUTO_REDISPATCH_META_KEY), 1)

    # ── I5 ─────────────────────────────────────────────────────────────

    @patch("tasks.dag_executor._run_subprocess_with_cancellation")
    @patch("tasks.dag_executor._read_log_tail")
    def test_auto_redispatch_records_counter_and_history_in_metadata(self, mock_tail, mock_run):
        """metadata.auto_redispatch_count==1 + auto_redispatch_history has 1 entry.

        Acceptance criterion #2: "Metadata trail shows attempt counter +
        failure class per auto-retry."
        """
        mock_run.return_value = (1, "odin_non_zero_exit")
        mock_tail.return_value = "Agent did not emit an ODIN-STATUS block."

        task = self.make_task(
            self.board,
            title="Counter + history",
            status=TaskStatus.EXECUTING,
            assignee=self.assignee,
            model_name="claude-sonnet-4-5",
        )

        execute_single_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.metadata.get(INFRA_AUTO_REDISPATCH_META_KEY), 1)
        history = task.metadata.get(INFRA_AUTO_REDISPATCH_HISTORY_KEY) or []
        self.assertEqual(len(history), 1)
        entry = history[0]
        # Each history entry names the failure class + the attempt number.
        # W4.4: the class label is now the policy-table class (truncation
        # — set by tag_failure_class on the FAILED transition) rather
        # than the legacy type-based classifier label.
        self.assertEqual(entry.get("class"), "truncation")
        self.assertEqual(entry.get("attempt"), 1)
        self.assertIn("at", entry)
        # Reason captured (truncated to 300 chars).
        self.assertIn("reason", entry)

    # ── I6 ─────────────────────────────────────────────────────────────

    @patch("tasks.dag_executor._run_subprocess_with_cancellation")
    @patch("tasks.dag_executor._read_log_tail")
    def test_auto_redispatch_writes_status_update_naming_class_and_attempt(
        self, mock_tail, mock_run,
    ):
        """The auto-redispatch STATUS_UPDATE comment names class + attempt.

        Acceptance criterion #1: "with a status_update comment naming the
        failure class and attempt number."
        """
        mock_run.return_value = (1, "odin_non_zero_exit")
        mock_tail.return_value = "Agent did not emit an ODIN-STATUS block."

        task = self.make_task(
            self.board,
            title="Naming comment",
            status=TaskStatus.EXECUTING,
            assignee=self.assignee,
            model_name="claude-sonnet-4-5",
        )

        execute_single_task(task.id)

        # Latest dag-executor status_update must name the failure class
        # AND the attempt number (1/2).
        comments = TaskComment.objects.filter(
            task=task,
            author_email="odin+dag-executor@system",
            comment_type=CommentType.STATUS_UPDATE,
        ).order_by("-created_at")

        contents = "\n---\n".join(c.content for c in comments)
        self.assertIn("Auto-redispatch 1/2", contents)
        # W4.4: the class label in the audit comment is now the
        # failure_class (truncation, set by the tagger) rather than
        # the legacy type-based classifier label.
        self.assertIn("truncation", contents)

    # ── I7 ─────────────────────────────────────────────────────────────

    @patch("tasks.dag_executor._run_subprocess_with_cancellation")
    @patch("tasks.dag_executor._read_log_tail")
    def test_auto_redispatch_reuses_f45_continuity_record(self, mock_tail, mock_run):
        """F45 continuity helper was called: assignee + model history + reason stamp.

        Reuse mandate from task #137: "reuse the existing rework-continuity
        path from #119".  The continuity helper writes TaskHistory rows
        for assignee + model with changed_by=system@taskit and stamps
        task.metadata['last_rework_reason'] = 'rework_continuity' plus
        'last_rework_at'.  Infra-class is preserved separately via the
        auto_redispatch_history entries (acceptance criterion #2).
        """
        mock_run.return_value = (1, "odin_non_zero_exit")
        mock_tail.return_value = "Agent did not emit an ODIN-STATUS block."

        task = self.make_task(
            self.board,
            title="Continuity reuse",
            status=TaskStatus.EXECUTING,
            assignee=self.assignee,
            model_name="claude-sonnet-4-5",
        )

        execute_single_task(task.id)

        task.refresh_from_db()

        # Assignee + model history rows from system@taskit (F45 helper).
        self.assertTrue(
            TaskHistory.objects.filter(
                task=task, field_name="assignee", changed_by="system@taskit",
            ).exists(),
            "F45 continuity helper must write an assignee history row.",
        )
        self.assertTrue(
            TaskHistory.objects.filter(
                task=task, field_name="model", changed_by="system@taskit",
            ).exists(),
            "F45 continuity helper must write a model history row.",
        )
        # The F45 helper stamps 'last_rework_reason' = 'rework_continuity'
        # and 'last_rework_at'.  Infra-class is recorded separately in
        # auto_redispatch_history (the F45 helper doesn't know about
        # infra classification — it just records the continuity decision).
        self.assertEqual(
            task.metadata.get("last_rework_reason"), "rework_continuity",
            "F45 continuity helper stamps last_rework_reason='rework_continuity' "
            "regardless of which pathway triggered it.",
        )
        self.assertIn("last_rework_at", task.metadata)
        # The infra-class signal lives in the auto_redispatch_history
        # entry — distinct from the F45 label.  W4.4: the class label
        # is now the policy-table class (truncation, set by
        # tag_failure_class) rather than the legacy type-based label.
        history = task.metadata.get(INFRA_AUTO_REDISPATCH_HISTORY_KEY) or []
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].get("class"), "truncation")


class CapRespected(APITestCase):
    """C1–C2: the 2-retries cap is hard, the cap-reached comment is idempotent."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(allow_project_root_execution=True)
        self.assignee = self.make_user(
            name="claude",
            email="claude@odin.agent",
        )

    # ── C1 ─────────────────────────────────────────────────────────────

    def test_auto_redispatch_cap_two_retries_max(self):
        """After 2 auto-retries (counter==2), a 3rd infra failure is NOT auto-retried.

        Pre-condition the counter to 2 (two prior auto-retries already
        happened) and verify the next infra-class FAILED transition
        does NOT flip back to IN_PROGRESS.  The task stays FAILED with
        a cap-reached flag and an audit comment.
        """
        task = self.make_task(
            self.board,
            title="Cap reached",
            status=TaskStatus.FAILED,
            assignee=self.assignee,
            model_name="claude-sonnet-4-5",
            metadata={
                "last_failure_type": "stale_execution",
                "last_failure_reason": "Process died before reporting result.",
                "last_failure_origin": "taskit_dag_executor",
                INFRA_AUTO_REDISPATCH_META_KEY: INFRA_AUTO_REDISPATCH_CAP,  # already capped
                INFRA_AUTO_REDISPATCH_HISTORY_KEY: [
                    {"class": "stale_execution", "attempt": 1, "at": "2026-07-05T00:00:00Z", "reason": "x"},
                    {"class": "stale_execution", "attempt": 2, "at": "2026-07-05T00:00:01Z", "reason": "x"},
                ],
            },
        )

        result = _maybe_auto_redispatch_infra_failure(task)
        self.assertFalse(result, "Cap must short-circuit further auto-retries.")

        task.refresh_from_db()
        # Task stays FAILED — operator must intervene.
        self.assertEqual(task.status, TaskStatus.FAILED)
        # Cap-reached flag set (first time only).
        self.assertTrue(task.metadata.get("auto_redispatch_cap_reached"))
        self.assertEqual(task.metadata.get("auto_redispatch_cap_reached_class"), "stale_execution")
        # Counter is unchanged — no further auto-retries happened.
        self.assertEqual(task.metadata.get(INFRA_AUTO_REDISPATCH_META_KEY), INFRA_AUTO_REDISPATCH_CAP)
        # A status_update comment narrates the cap.
        cap_comments = TaskComment.objects.filter(
            task=task,
            author_email="odin+dag-executor@system",
            comment_type=CommentType.STATUS_UPDATE,
        ).order_by("-created_at")
        cap_content = "\n---\n".join(c.content for c in cap_comments)
        self.assertIn("Auto-redispatch cap reached", cap_content)
        self.assertIn(f"{INFRA_AUTO_REDISPATCH_CAP}/{INFRA_AUTO_REDISPATCH_CAP}", cap_content)

    # ── C2 ─────────────────────────────────────────────────────────────

    def test_auto_redispatch_cap_reached_idempotent(self):
        """Calling the hook repeatedly on a cap-reached task does NOT spam comments.

        Without the idempotency guard, every poll_and_execute cycle
        would re-post the cap-reached comment and bury the timeline.
        """
        task = self.make_task(
            self.board,
            title="Idempotent cap",
            status=TaskStatus.FAILED,
            assignee=self.assignee,
            model_name="claude-sonnet-4-5",
            metadata={
                "last_failure_type": "stale_execution",
                "last_failure_reason": "Process died.",
                "last_failure_origin": "taskit_dag_executor",
                INFRA_AUTO_REDISPATCH_META_KEY: INFRA_AUTO_REDISPATCH_CAP,
                "auto_redispatch_cap_reached": True,  # already announced
            },
        )

        baseline_count = TaskComment.objects.filter(
            task=task, author_email="odin+dag-executor@system",
            comment_type=CommentType.STATUS_UPDATE,
        ).count()

        # Hit the hook again — should be a no-op for comments.
        _maybe_auto_redispatch_infra_failure(task)
        _maybe_auto_redispatch_infra_failure(task)
        _maybe_auto_redispatch_infra_failure(task)

        after_count = TaskComment.objects.filter(
            task=task, author_email="odin+dag-executor@system",
            comment_type=CommentType.STATUS_UPDATE,
        ).count()
        self.assertEqual(
            after_count, baseline_count,
            "Cap-reached must be idempotent — no duplicate cap comments.",
        )


class RealFailuresUntouched(APITestCase):
    """R1–R5: real failures are NEVER auto-retried — those need human judgment.

    The complement of InfraFailuresAutoRedispatched: every failure
    classification that represents genuine agent work (or a real
    infrastructure problem the operator must fix) must NOT trigger the
    auto-redispatch hook.  Otherwise we'd silently mask auth errors,
    timeouts, worktree misconfigurations, and agent-reported FAILEDs.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board(allow_project_root_execution=True)
        self.assignee = self.make_user(
            name="claude",
            email="claude@odin.agent",
        )

    # ── R1 ─────────────────────────────────────────────────────────────

    @patch("tasks.dag_executor._run_subprocess_with_cancellation")
    @patch("tasks.dag_executor._read_log_tail")
    def test_agent_reported_failure_in_odin_status_not_retried(self, mock_tail, mock_run):
        """Agent reported FAILED in ODIN-STATUS → NOT auto-retried.

        The agent had a chance to emit ODIN-STATUS and chose FAILED.  That
        is the explicit "this task can't be done as specified" signal —
        auto-retrying would just consume tokens and reproduce the same
        failure.  Real failure, leave FAILED.
        """
        mock_run.return_value = (1, "odin_non_zero_exit")
        mock_tail.return_value = "Agent explicitly reported FAILED in ODIN-STATUS block."

        task = self.make_task(
            self.board,
            title="Agent said FAILED",
            status=TaskStatus.EXECUTING,
            assignee=self.assignee,
            model_name="claude-sonnet-4-5",
        )

        execute_single_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)
        # No auto-redispatch counter touched.
        self.assertNotIn(INFRA_AUTO_REDISPATCH_META_KEY, task.metadata)
        # The dag-executor failure comment is there, but NOT the auto-redispatch one.
        comments = TaskComment.objects.filter(
            task=task,
            author_email="odin+dag-executor@system",
            comment_type=CommentType.STATUS_UPDATE,
        )
        for c in comments:
            self.assertNotIn("Auto-redispatch", c.content)

    # ── R2 ─────────────────────────────────────────────────────────────

    @patch("tasks.dag_executor._run_subprocess_with_cancellation")
    @patch("tasks.dag_executor._read_log_tail")
    def test_backend_auth_failure_not_retried(self, mock_tail, mock_run):
        """backend_auth_failure → NOT auto-retried (real config problem).

        Auto-retrying an auth failure is pointless — the next attempt
        will hit the same 401.  The operator needs to fix credentials.
        """
        mock_run.return_value = (1, "odin_non_zero_exit")
        mock_tail.return_value = (
            "Authentication error: TaskIt returned 401 Unauthorized for "
            "http://localhost:8000/tasks/?board_id=1"
        )

        task = self.make_task(
            self.board,
            title="Auth failure",
            status=TaskStatus.EXECUTING,
            assignee=self.assignee,
            model_name="claude-sonnet-4-5",
        )

        execute_single_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.metadata.get("last_failure_type"), "backend_auth_failure")
        self.assertNotIn(INFRA_AUTO_REDISPATCH_META_KEY, task.metadata)

    # ── R3 ─────────────────────────────────────────────────────────────

    @patch("tasks.dag_executor._run_subprocess_with_cancellation")
    def test_timeout_failure_not_retried(self, mock_run):
        """timeout → NOT auto-retried (real deadline problem).

        Timeouts often indicate runaway code, oversized scope, or an
        unresponsive downstream — retrying blind burns the wall-clock
        budget twice.  Operator must investigate first.
        """
        mock_run.return_value = (-1, "timeout")

        task = self.make_task(
            self.board,
            title="Timeout",
            status=TaskStatus.EXECUTING,
            assignee=self.assignee,
            model_name="claude-sonnet-4-5",
        )

        execute_single_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.metadata.get("last_failure_type"), "timeout")
        self.assertNotIn(INFRA_AUTO_REDISPATCH_META_KEY, task.metadata)

    # ── R4 ─────────────────────────────────────────────────────────────

    def test_missing_worktree_failure_not_retried(self):
        """missing_worktree → NOT auto-retried (board-config problem).

        The board needs allow_project_root_execution=True or the task
        needs a spec branch.  Auto-retrying without configuration change
        just FAILEDs the task again.  Operator must fix the config.
        """
        task = self.make_task(
            self.board,
            title="No worktree",
            status=TaskStatus.FAILED,
            assignee=self.assignee,
            model_name="claude-sonnet-4-5",
            metadata={
                "last_failure_type": "missing_worktree",
                "last_failure_reason": "No task worktree could be created.",
                "last_failure_origin": "taskit_dag_executor",
            },
        )

        result = _maybe_auto_redispatch_infra_failure(task)
        self.assertFalse(result)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertNotIn(INFRA_AUTO_REDISPATCH_META_KEY, task.metadata)

    # ── R5 ─────────────────────────────────────────────────────────────

    @patch("tasks.dag_executor._run_subprocess_with_cancellation")
    @patch("tasks.dag_executor._read_log_tail")
    def test_agent_execution_failure_other_reason_not_retried(self, mock_tail, mock_run):
        """agent_execution_failure with a real reason → NOT auto-retried.

        The bare 'truncated' / 'terminated silently' substrings are
        infra; an ImportError or assertion failure is real work the
        agent surfaced.  Retrying would just burn tokens reproducing
        the same bug.
        """
        mock_run.return_value = (1, "odin_non_zero_exit")
        mock_tail.return_value = "ImportError: cannot import name 'foo' from 'bar'"

        task = self.make_task(
            self.board,
            title="Import error",
            status=TaskStatus.EXECUTING,
            assignee=self.assignee,
            model_name="claude-sonnet-4-5",
        )

        execute_single_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.metadata.get("last_failure_type"), "agent_execution_failure")
        # Reason doesn't contain any infra signature — no auto-retry.
        self.assertNotIn(INFRA_AUTO_REDISPATCH_META_KEY, task.metadata)


class IsInfraFailureClassifier(APITestCase):
    """Pin the `_is_infra_failure` classifier's contract.

    These tests exercise the classifier directly so the regex/keyword
    set can't drift without a failing test.  If someone widens the
    infra classification without intending to, the matrix here catches
    it.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def _task_with_failure(self, failure_type, reason):
        return self.make_task(
            self.board,
            title="Classifier pin",
            status=TaskStatus.FAILED,
            metadata={
                "last_failure_type": failure_type,
                "last_failure_reason": reason,
                "last_failure_origin": "taskit_dag_executor",
            },
        )

    def test_stale_execution_is_infra(self):
        ok, label = _is_infra_failure(
            self._task_with_failure("stale_execution", "Process died.")
        )
        self.assertTrue(ok)
        self.assertEqual(label, "stale_execution")

    def test_no_odin_status_signature_is_infra(self):
        ok, label = _is_infra_failure(
            self._task_with_failure(
                "agent_execution_failure",
                "Agent did not emit an ODIN-STATUS block.",
            )
        )
        self.assertTrue(ok)
        self.assertEqual(label, "agent_execution_failure:no_odin_status")

    def test_truncated_signature_is_infra(self):
        ok, label = _is_infra_failure(
            self._task_with_failure(
                "agent_execution_failure",
                "Response truncated at 8192 tokens (output cap).",
            )
        )
        self.assertTrue(ok)

    def test_terminated_silently_signature_is_infra(self):
        ok, label = _is_infra_failure(
            self._task_with_failure(
                "agent_execution_failure",
                "Process terminated silently before completing.",
            )
        )
        self.assertTrue(ok)

    def test_backend_auth_failure_is_not_infra(self):
        ok, label = _is_infra_failure(
            self._task_with_failure(
                "backend_auth_failure",
                "TaskIt returned 401 Unauthorized.",
            )
        )
        self.assertFalse(ok)
        self.assertIsNone(label)

    def test_timeout_is_not_infra(self):
        ok, label = _is_infra_failure(
            self._task_with_failure("timeout", "Task execution timed out.")
        )
        self.assertFalse(ok)
        self.assertIsNone(label)

    def test_missing_worktree_is_not_infra(self):
        ok, label = _is_infra_failure(
            self._task_with_failure("missing_worktree", "No task worktree.")
        )
        self.assertFalse(ok)
        self.assertIsNone(label)

    def test_real_agent_failure_is_not_infra(self):
        """ImportError / assertion failure / etc. is real work, not infra."""
        ok, label = _is_infra_failure(
            self._task_with_failure(
                "agent_execution_failure",
                "AssertionError: expected 200, got 500",
            )
        )
        self.assertFalse(ok)
        self.assertIsNone(label)

    def test_empty_failure_type_is_not_infra(self):
        ok, label = _is_infra_failure(self._task_with_failure("", ""))
        self.assertFalse(ok)
        self.assertIsNone(label)


class NoAssigneeDoesNotRedispatch(APITestCase):
    """Auto-redispatch must not fire if the task has no assignee.

    Without an assignee the system cannot dispatch — the original
    failure comment remains and the operator must intervene.  Guards
    against a path that flips status to IN_PROGRESS while leaving
    the task undispatchable (which would just FAILED it again on the
    next poll cycle with no_worktree_no_optin / no_assignee).
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board(allow_project_root_execution=True)

    def test_no_assignee_skips_redispatch(self):
        task = self.make_task(
            self.board,
            title="No assignee",
            status=TaskStatus.FAILED,
            assignee=None,
            metadata={
                "last_failure_type": "stale_execution",
                "last_failure_reason": "Process died.",
                "last_failure_origin": "taskit_dag_executor",
            },
        )

        result = _maybe_auto_redispatch_infra_failure(task)
        self.assertFalse(result)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertNotIn(INFRA_AUTO_REDISPATCH_META_KEY, task.metadata)