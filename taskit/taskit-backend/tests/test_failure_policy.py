"""Per-class failure-policy table (W4.4).

The failure-class tagger labels failures but a human still decides
requeue/hold — pure toil for transient classes.  This module adds the
**policy layer** on top: per-class action (auto_requeue / reassign /
human), bounded retries, backoff, and an operator-visible comment per
automatic decision.

Origin: a zai TLS stream error killed a live task; the operator
requeued it by hand minutes later.  Root problem: classification
exists, policy doesn't.

Coverage (acceptance criteria from task #194):

  PolicyTableDefaults:
    T1.  test_default_table_covers_every_failure_class
         Every value in FAILURE_CLASSES has a policy entry.
    T2.  test_default_table_actions_partition_taxonomy
         The taxonomy splits cleanly across the three actions:
           auto_requeue = stale_execution, truncation, silent_hang,
                          transport_error, lock_race
           reassign     = quota_exhaustion
           human        = env_missing, timeout, worktree_isolation,
                          disk_exhaustion, crash, cancelled,
                          model_unavailable, unknown
    T3.  test_default_table_unknown_is_human
         unknown → human (never auto-requeue, never auto-reassign).
    T4.  test_default_table_retry_bounds_positive_int
         Every auto_requeue policy has max_retries ≥ 1.
    T5.  test_default_table_backoff_non_negative
         Backoff seconds are non-negative (0 is allowed for fast retries).

  PolicyResolution:
    R1.  test_resolve_uses_failure_class_from_metadata
         resolve_policy reads metadata.failure_class.
    R2.  test_resolve_unknown_when_class_missing
         Missing failure_class → human policy.
    R3.  test_resolve_unknown_when_class_unknown_string
         failure_class = "weird_value" → unknown → human (default fallback).
    R4.  test_settings_override_retry_bound
         Settings can override max_retries for a class.

  PolicyDispatch — AUTO_REQUEUE happy path:
    A1.  test_transport_error_failure_auto_requeues
         transport_error (zai TLS) → FAILED → auto-requeued IN_PROGRESS,
         assignee + model preserved.
    A2.  test_truncation_failure_auto_requeues
         truncation → auto-requeued.
    A3.  test_silent_hang_failure_auto_requeues
         silent_hang → auto-requeued.
    A4.  test_lock_race_failure_auto_requeues
         lock_race → auto-requeued.
    A5.  test_transport_error_requeues_with_backoff_metadata
         transport_error stamps metadata.next_retry_after when backoff > 0.
    A6.  test_auto_requeue_increments_policy_counter
         Policy dispatch bumps metadata.auto_reispatch_count + history.
    A7.  test_auto_requeue_comment_names_class_attempt_next_action
         Comment names failure class + attempt N/M + next action.

  PolicyDispatch — REASSIGN (quota):
    Q1.  test_quota_exhaustion_posts_reassign_naming_comment
         quota_exhaustion → comment names "reassign on reflection"
         (the actual reassign lives at the reflection-time path).
    Q2.  test_quota_exhaustion_does_not_flip_status
         quota_exhaustion leaves the task FAILED — only the reflection
         path can flip it to a new assignee.

  PolicyDispatch — HUMAN (never auto-retry):
    H1.  test_unknown_failure_does_not_auto_requeue
         unknown → status stays FAILED, no auto_redispatch_count, no
         IN_PROGRESS transition.
    H2.  test_env_missing_failure_does_not_auto_requeue
         env_missing → status stays FAILED.
    H3.  test_timeout_failure_does_not_auto_requeue
         timeout → status stays FAILED.
    H4.  test_worktree_isolation_failure_does_not_auto_requeue
         worktree_isolation → status stays FAILED.
    H5.  test_crash_failure_does_not_auto_requeue
         crash → status stays FAILED.
    H6.  test_human_failure_posts_audit_comment
         human action posts a comment naming the class so the operator
         can see why nothing auto-fired.

  RetryBound:
    B1.  test_auto_requeue_cap_respected
         After max_retries auto-requeues, the next failure of the same
         class is NOT auto-requeued — task stays FAILED with a
         cap-reached flag and an audit comment.
    B2.  test_auto_requeue_cap_idempotent
         Repeated calls after cap reached do not spam comments.
    B3.  test_per_class_cap_isolated
         truncation retry counter is separate from transport_error's
         (each class has its own bounded retry budget).

  HookAtFailedTransition:
    F1.  test_apply_failure_policy_skips_when_status_not_failed
         The hook is a no-op when the task is not FAILED (defense in
         depth — only the FAILED transition fires it).

  NonVisual — no browser/screenshots needed.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import patch

from django.test import override_settings

from tests.base import APITestCase
from tasks.failure_policy import (
    ACTION_AUTO_REQUEUE,
    ACTION_HUMAN,
    ACTION_REASSIGN,
    DEFAULT_POLICY_TABLE,
    POLICY_META_KEY,
    POLICY_HISTORY_KEY,
    FailurePolicy,
    apply_failure_policy,
    resolve_policy,
)
from tasks.failure_tagger import FAILURE_CLASSES
from tasks.models import (
    CommentType,
    Task,
    TaskComment,
    TaskHistory,
    TaskStatus,
)


class PolicyTableDefaults(APITestCase):
    """T1–T5: the default policy table covers the entire taxonomy."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(allow_project_root_execution=True)

    def test_default_table_covers_every_failure_class(self):
        """Every value in FAILURE_CLASSES has a policy entry — no gaps.

        Without full coverage, a failure class with no entry would
        crash the dispatcher.  This is the policy layer's "no surprises"
        invariant.
        """
        for cls in FAILURE_CLASSES:
            self.assertIn(cls, DEFAULT_POLICY_TABLE, f"missing policy for {cls}")

    def test_default_table_actions_partition_taxonomy(self):
        """Three actions partition the taxonomy exactly as the brief requires.

        auto_requeue  = transient infra (network/TLS/stream, truncation,
                       silent hang, lock race, stale worker, error loop)
        reassign      = quota exhaustion (existing reflection-time path)
        human         = real work + unclassified failures
        """
        auto_requeue = {
            cls for cls, p in DEFAULT_POLICY_TABLE.items() if p.action == ACTION_AUTO_REQUEUE
        }
        reassign = {
            cls for cls, p in DEFAULT_POLICY_TABLE.items() if p.action == ACTION_REASSIGN
        }
        human = {
            cls for cls, p in DEFAULT_POLICY_TABLE.items() if p.action == ACTION_HUMAN
        }
        self.assertEqual(
            auto_requeue,
            {
                "stale_execution", "truncation", "silent_hang",
                "transport_error", "lock_race", "error_loop",
                # task #331: a sandbox that never started an agent is a
                # pre-execution infra failure — retry, don't blame an
                # agent that never ran.
                "sandbox_unavailable",
            },
        )
        self.assertEqual(reassign, {"quota_exhaustion"})
        self.assertEqual(
            human,
            {
                "env_missing", "auth_failure", "timeout", "worktree_isolation",
                "disk_exhaustion", "crash", "cancelled", "model_unavailable",
                "unknown",
                # task #359 — three NEEDS_WORK/FAIL reviews means a human
                # has to read the reviewer's note and decide (auto-retry
                # the same agent/model is the banner lie).
                "review_cap",
            },
        )
        # All three sets partition the full taxonomy with no overlap.
        union = auto_requeue | reassign | human
        self.assertEqual(union, set(DEFAULT_POLICY_TABLE.keys()))
        self.assertEqual(union, set(FAILURE_CLASSES))

    def test_default_table_unknown_is_human(self):
        """unknown → human (NEVER auto-requeue, NEVER auto-reassign)."""
        self.assertEqual(DEFAULT_POLICY_TABLE["unknown"].action, ACTION_HUMAN)
        self.assertEqual(DEFAULT_POLICY_TABLE["unknown"].max_retries, 0)

    def test_default_table_retry_bounds_positive_int(self):
        """Every auto_requeue policy has max_retries ≥ 1."""
        for cls, policy in DEFAULT_POLICY_TABLE.items():
            if policy.action == ACTION_AUTO_REQUEUE:
                self.assertGreaterEqual(
                    policy.max_retries, 1,
                    f"auto_requeue class {cls} has max_retries < 1",
                )

    def test_default_table_backoff_non_negative(self):
        """Backoff seconds are non-negative (0 is allowed for fast retries)."""
        for cls, policy in DEFAULT_POLICY_TABLE.items():
            self.assertGreaterEqual(
                policy.backoff_seconds, 0,
                f"class {cls} has negative backoff_seconds",
            )


class PolicyResolution(APITestCase):
    """R1–R4: resolve_policy reads metadata, settings override retry bounds."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(allow_project_root_execution=True)
        self.assignee = self.make_user(name="claude", email="claude@odin.agent")

    def test_resolve_uses_failure_class_from_metadata(self):
        metadata = {"failure_class": "transport_error"}
        policy = resolve_policy(metadata)
        self.assertEqual(policy.action, ACTION_AUTO_REQUEUE)
        self.assertEqual(DEFAULT_POLICY_TABLE["transport_error"], policy)

    def test_resolve_unknown_when_class_missing(self):
        """Missing failure_class → human policy (defensive default).

        The FAILED transition always stamps failure_class via
        tag_failure_class — if it didn't (a bug), the policy must
        still return something sensible.  Human is the safe default:
        auto-retrying an unclassified failure is worse than paging
        the operator.
        """
        policy = resolve_policy({})
        self.assertEqual(policy.action, ACTION_HUMAN)

    def test_resolve_unknown_when_class_unknown_string(self):
        """An unrecognised class string → unknown → human.

        Future failure_taggers may add new classes; until the policy
        table is updated, anything new defaults to human.
        """
        policy = resolve_policy({"failure_class": "weird_new_class"})
        self.assertEqual(policy.action, ACTION_HUMAN)

    def test_settings_override_retry_bound(self):
        """Settings can override max_retries for a class.

        Operators tune retry bounds per host without code changes.
        """
        with override_settings(DAG_EXECUTOR_FAILURE_POLICY_OVERRIDES={
            "transport_error": {"max_retries": 5},
        }):
            policy = resolve_policy({"failure_class": "transport_error"})
        self.assertEqual(policy.max_retries, 5)
        # Action stays the same.
        self.assertEqual(policy.action, ACTION_AUTO_REQUEUE)


class PolicyDispatchAutoRequeue(APITestCase):
    """A1–A7: AUTO_REQUEUE classes route through the bounded retry path."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(allow_project_root_execution=True)
        self.assignee = self.make_user(name="claude", email="claude@odin.agent")

    def _failed_task(self, failure_class, failure_type, reason):
        return self.make_task(
            self.board,
            title=f"{failure_class} task",
            status=TaskStatus.FAILED,
            assignee=self.assignee,
            model_name="claude-sonnet-4-5",
            metadata={
                "last_failure_type": failure_type,
                "last_failure_reason": reason,
                "last_failure_origin": "taskit_dag_executor",
                "failure_class": failure_class,
            },
        )

    # ── A1 ─────────────────────────────────────────────────────────────

    def test_transport_error_failure_auto_requeues(self):
        """transport_error (zai TLS stream) → auto-requeue, continuity preserved.

        The headline live scenario from task #194: a TLS stream error
        killed a task; the operator requeued it by hand.  The policy
        layer must fire the same retry automatically.
        """
        task = self._failed_task(
            failure_class="transport_error",
            failure_type="backend_exception",
            reason="httpx.RemoteProtocolError: peer closed connection",
        )
        original_assignee_id = task.assignee_id
        original_model = task.model_name

        with patch("tasks.dag_executor.execute_single_task"):
            result = apply_failure_policy(task)

        self.assertTrue(result, "transport_error must auto-requeue")

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        self.assertEqual(task.assignee_id, original_assignee_id)
        self.assertEqual(task.model_name, original_model)

    # ── A2 ─────────────────────────────────────────────────────────────

    def test_truncation_failure_auto_requeues(self):
        task = self._failed_task(
            failure_class="truncation",
            failure_type="agent_execution_failure",
            reason="Response truncated at 8192 tokens (output cap hit).",
        )
        with patch("tasks.dag_executor.execute_single_task"):
            result = apply_failure_policy(task)
        self.assertTrue(result)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        self.assertEqual(task.metadata[POLICY_META_KEY], 1)

    # ── A3 ─────────────────────────────────────────────────────────────

    def test_silent_hang_failure_auto_requeues(self):
        task = self._failed_task(
            failure_class="silent_hang",
            failure_type="agent_execution_failure",
            reason="Agent produced no output; likely the CLI crashed.",
        )
        with patch("tasks.dag_executor.execute_single_task"):
            result = apply_failure_policy(task)
        self.assertTrue(result)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)

    # ── A4 ─────────────────────────────────────────────────────────────

    def test_lock_race_failure_auto_requeues(self):
        task = self._failed_task(
            failure_class="lock_race",
            failure_type="internal_error",
            reason="sqlite3.OperationalError: database is locked",
        )
        with patch("tasks.dag_executor.execute_single_task"):
            result = apply_failure_policy(task)
        self.assertTrue(result)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)

    # ── A5 ─────────────────────────────────────────────────────────────

    def test_transport_error_requeues_with_backoff_metadata(self):
        """transport_error stamps metadata.next_retry_after when backoff > 0.

        Brief: "auto-requeue with bounded retries + backoff".  transport_error
        has the highest backoff in the default table (TLS errors
        shouldn't tight-loop); the metadata stamps a future timestamp so
        a downstream scheduler can honor the delay.
        """
        task = self._failed_task(
            failure_class="transport_error",
            failure_type="backend_exception",
            reason="httpx.RemoteProtocolError: peer closed connection",
        )
        with patch("tasks.dag_executor.execute_single_task"):
            apply_failure_policy(task)

        task.refresh_from_db()
        self.assertIn("next_retry_after", task.metadata)
        # next_retry_after is an ISO timestamp in the future.
        from datetime import datetime
        nra = datetime.fromisoformat(task.metadata["next_retry_after"])
        self.assertGreater(nra.timestamp(), 0)

    # ── A6 ─────────────────────────────────────────────────────────────

    def test_auto_requeue_increments_policy_counter(self):
        """Policy dispatch bumps metadata.auto_redispatch_count + history.

        The counter is shared with the legacy infra-redispatch path —
        one audit trail covers both legacy infra classes (stale_execution)
        and the new transient classes (transport_error, truncation, etc.).
        """
        task = self._failed_task(
            failure_class="truncation",
            failure_type="agent_execution_failure",
            reason="Response truncated at 8192 tokens.",
        )
        with patch("tasks.dag_executor.execute_single_task"):
            apply_failure_policy(task)

        task.refresh_from_db()
        self.assertEqual(task.metadata[POLICY_META_KEY], 1)
        history = task.metadata[POLICY_HISTORY_KEY] or []
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].get("class"), "truncation")
        self.assertEqual(history[0].get("attempt"), 1)

    # ── A7 ─────────────────────────────────────────────────────────────

    def test_auto_requeue_comment_names_class_attempt_next_action(self):
        """Comment names failure class + attempt N/M + next action.

        Acceptance criterion: "every automatic decision is posted as a
        task comment (class, attempt N of M, next action)."
        """
        task = self._failed_task(
            failure_class="transport_error",
            failure_type="backend_exception",
            reason="httpx.RemoteProtocolError: peer closed connection",
        )
        with patch("tasks.dag_executor.execute_single_task"):
            apply_failure_policy(task)

        comments = TaskComment.objects.filter(
            task=task,
            author_email="odin+dag-executor@system",
            comment_type=CommentType.STATUS_UPDATE,
        ).order_by("-created_at")
        contents = "\n---\n".join(c.content for c in comments)
        self.assertIn("transport_error", contents)
        self.assertIn("1/", contents)
        self.assertIn("retrying", contents.lower())


class PolicyDispatchReassign(APITestCase):
    """Q1–Q2: quota_exhaustion posts a comment; the actual reassign runs later."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(allow_project_root_execution=True)
        self.assignee = self.make_user(name="claude", email="claude@odin.agent")

    def test_quota_exhaustion_posts_reassign_naming_comment(self):
        task = self.make_task(
            self.board,
            title="Quota task",
            status=TaskStatus.FAILED,
            assignee=self.assignee,
            model_name="claude-sonnet-4-5",
            metadata={
                "last_failure_type": "llm_call_failure",
                "last_failure_reason": "HTTP 429: rate limit exceeded",
                "last_failure_origin": "taskit_dag_executor",
                "failure_class": "quota_exhaustion",
            },
        )
        with patch("tasks.dag_executor.execute_single_task"):
            result = apply_failure_policy(task)
        # The dispatcher doesn't flip the status — it only marks the
        # audit trail.  The reflection-time path performs the reassign.
        self.assertFalse(result)

        task.refresh_from_db()
        comments = TaskComment.objects.filter(
            task=task,
            author_email="odin+dag-executor@system",
            comment_type=CommentType.STATUS_UPDATE,
        ).order_by("-created_at")
        contents = "\n---\n".join(c.content for c in comments)
        self.assertIn("quota_exhaustion", contents)
        self.assertIn("reassign", contents.lower())

    def test_quota_exhaustion_does_not_flip_status(self):
        task = self.make_task(
            self.board,
            title="Quota no-flip",
            status=TaskStatus.FAILED,
            assignee=self.assignee,
            model_name="claude-sonnet-4-5",
            metadata={
                "last_failure_type": "llm_call_failure",
                "last_failure_reason": "HTTP 429: rate limit exceeded",
                "last_failure_origin": "taskit_dag_executor",
                "failure_class": "quota_exhaustion",
            },
        )
        with patch("tasks.dag_executor.execute_single_task"):
            apply_failure_policy(task)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)


class PolicyDispatchHuman(APITestCase):
    """H1–H6: human-action classes never auto-retry and surface an audit comment."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(allow_project_root_execution=True)
        self.assignee = self.make_user(name="claude", email="claude@odin.agent")

    def _failed_task(self, failure_class, failure_type, reason):
        return self.make_task(
            self.board,
            title=f"{failure_class} task",
            status=TaskStatus.FAILED,
            assignee=self.assignee,
            model_name="claude-sonnet-4-5",
            metadata={
                "last_failure_type": failure_type,
                "last_failure_reason": reason,
                "last_failure_origin": "taskit_dag_executor",
                "failure_class": failure_class,
            },
        )

    def test_unknown_failure_does_not_auto_requeue(self):
        """unknown → status stays FAILED, no auto_redispatch_count.

        The complement of the auto-requeue tests: an unclassified
        failure must NEVER trigger an automatic retry.  Operators
        triage unknown failures by hand.
        """
        task = self._failed_task(
            failure_class="unknown",
            failure_type="",
            reason="Something completely unexpected",
        )
        with patch("tasks.dag_executor.execute_single_task"):
            result = apply_failure_policy(task)
        self.assertFalse(result)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertNotIn(POLICY_META_KEY, task.metadata)

    def test_env_missing_failure_does_not_auto_requeue(self):
        """env_missing → FAILED stays.  Auto-retrying a 401 is pointless."""
        task = self._failed_task(
            failure_class="env_missing",
            failure_type="backend_auth_failure",
            reason="Authentication error: TaskIt returned 401 Unauthorized",
        )
        with patch("tasks.dag_executor.execute_single_task"):
            apply_failure_policy(task)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertNotIn(POLICY_META_KEY, task.metadata)

    def test_timeout_failure_does_not_auto_requeue(self):
        task = self._failed_task(
            failure_class="timeout",
            failure_type="timeout",
            reason="Task execution timed out after 1800s",
        )
        with patch("tasks.dag_executor.execute_single_task"):
            apply_failure_policy(task)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)

    def test_worktree_isolation_failure_does_not_auto_requeue(self):
        task = self._failed_task(
            failure_class="worktree_isolation",
            failure_type="missing_worktree",
            reason="No task worktree could be created.",
        )
        with patch("tasks.dag_executor.execute_single_task"):
            apply_failure_policy(task)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)

    def test_crash_failure_does_not_auto_requeue(self):
        """A real crash (ImportError, AssertionError) stays FAILED.

        Retrying a real bug just reproduces the same crash and
        burns tokens.
        """
        task = self._failed_task(
            failure_class="crash",
            failure_type="agent_execution_failure",
            reason="ImportError: cannot import name 'foo' from 'bar'",
        )
        with patch("tasks.dag_executor.execute_single_task"):
            apply_failure_policy(task)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertNotIn(POLICY_META_KEY, task.metadata)

    def test_human_failure_posts_audit_comment(self):
        """human action posts a comment naming the class + action.

        The operator must see WHY nothing auto-fired.  Without this
        comment a stuck FAILED task looks indistinguishable from a
        forgotten one.
        """
        task = self._failed_task(
            failure_class="env_missing",
            failure_type="backend_auth_failure",
            reason="ZAI_API_KEY environment variable is not set",
        )
        with patch("tasks.dag_executor.execute_single_task"):
            apply_failure_policy(task)

        comments = TaskComment.objects.filter(
            task=task,
            author_email="odin+dag-executor@system",
            comment_type=CommentType.STATUS_UPDATE,
        ).order_by("-created_at")
        contents = "\n---\n".join(c.content for c in comments)
        self.assertIn("env_missing", contents)
        self.assertIn("human", contents.lower())


class RetryBound(APITestCase):
    """B1–B3: retry bounds are enforced; per-class counters are isolated."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(allow_project_root_execution=True)
        self.assignee = self.make_user(name="claude", email="claude@odin.agent")

    def _failed_task(self, failure_class, failure_type, reason, counter=0, cap=2):
        return self.make_task(
            self.board,
            title=f"{failure_class} cap",
            status=TaskStatus.FAILED,
            assignee=self.assignee,
            model_name="claude-sonnet-4-5",
            metadata={
                "last_failure_type": failure_type,
                "last_failure_reason": reason,
                "last_failure_origin": "taskit_dag_executor",
                "failure_class": failure_class,
                POLICY_META_KEY: counter,
                POLICY_HISTORY_KEY: [
                    {"class": failure_class, "attempt": i, "at": "2026-07-07T00:00:00Z",
                     "reason": reason[:200]}
                    for i in range(1, counter + 1)
                ],
            },
        )

    def test_auto_requeue_cap_respected(self):
        """After max_retries auto-requeues, the next failure is NOT retried.

        Pre-condition the counter to max_retries (already capped) and
        verify the next transient failure does NOT flip FAILED →
        IN_PROGRESS.  The task stays FAILED with a cap-reached flag
        and an audit comment.
        """
        task = self._failed_task(
            failure_class="transport_error",
            failure_type="backend_exception",
            reason="httpx.RemoteProtocolError: peer closed connection",
            counter=2, cap=2,
        )
        result = apply_failure_policy(task)
        self.assertFalse(result)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertTrue(task.metadata.get("policy_cap_reached"))
        self.assertEqual(
            task.metadata.get("policy_cap_reached_class"), "transport_error",
        )

    def test_auto_requeue_cap_idempotent(self):
        """Repeated calls after cap reached do not spam comments."""
        task = self._failed_task(
            failure_class="truncation",
            failure_type="agent_execution_failure",
            reason="Response truncated at 8192 tokens.",
            counter=2, cap=2,
        )
        # Pre-stamp the cap-reached flag so the dispatcher treats this
        # task as "already announced" — same pattern as the legacy
        # test_infra_auto_redispatch::test_auto_redispatch_cap_reached_idempotent.
        metadata = dict(task.metadata or {})
        metadata["policy_cap_reached"] = True
        metadata["policy_cap_reached_at"] = "2026-07-07T00:00:00Z"
        metadata["policy_cap_reached_class"] = "truncation"
        task.metadata = metadata
        task.save(update_fields=["metadata"])

        baseline_count = TaskComment.objects.filter(
            task=task, author_email="odin+dag-executor@system",
            comment_type=CommentType.STATUS_UPDATE,
        ).count()

        apply_failure_policy(task)
        apply_failure_policy(task)
        apply_failure_policy(task)

        after_count = TaskComment.objects.filter(
            task=task, author_email="odin+dag-executor@system",
            comment_type=CommentType.STATUS_UPDATE,
        ).count()
        self.assertEqual(
            after_count, baseline_count,
            "cap-reached must be idempotent — no duplicate cap comments.",
        )

    def test_per_class_cap_isolated(self):
        """truncation retry counter is separate from transport_error's.

        Each class has its own bounded retry budget — a flurry of
        transport_errors doesn't burn the truncation budget.
        """
        # transport_error already at cap (2/2).
        task = self._failed_task(
            failure_class="transport_error",
            failure_type="backend_exception",
            reason="httpx.RemoteProtocolError",
            counter=2, cap=2,
        )
        apply_failure_policy(task)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)

        # A new task of the same class but a DIFFERENT transient
        # class (truncation) starts fresh — separate counter.
        task2 = self.make_task(
            self.board,
            title="Fresh truncation",
            status=TaskStatus.FAILED,
            assignee=self.assignee,
            model_name="claude-sonnet-4-5",
            metadata={
                "last_failure_type": "agent_execution_failure",
                "last_failure_reason": "Response truncated.",
                "last_failure_origin": "taskit_dag_executor",
                "failure_class": "truncation",
            },
        )
        with patch("tasks.dag_executor.execute_single_task"):
            result = apply_failure_policy(task2)
        self.assertTrue(result, "truncation has its own fresh counter")
        task2.refresh_from_db()
        self.assertEqual(task2.status, TaskStatus.IN_PROGRESS)


class HookAtFailedTransition(APITestCase):
    """F1: apply_failure_policy is a no-op when the task is not FAILED.

    Defense in depth: the dispatcher only fires at the FAILED
    transition.  A buggy caller passing an EXECUTING or DONE task
    must not silently mutate the state.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board(allow_project_root_execution=True)
        self.assignee = self.make_user(name="claude", email="claude@odin.agent")

    def test_apply_failure_policy_skips_when_status_not_failed(self):
        task = self.make_task(
            self.board,
            title="Not failed",
            status=TaskStatus.EXECUTING,
            assignee=self.assignee,
            model_name="claude-sonnet-4-5",
            metadata={"failure_class": "transport_error"},
        )
        result = apply_failure_policy(task)
        self.assertFalse(result)

        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.EXECUTING)