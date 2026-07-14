"""Deterministic failure-class tagger (BACKLOG item 4).

Coverage:
  ClassifyFailureTaxonomy — one test per taxonomy class with realistic
    trace/exit fixtures, plus the unknown fallback.
  HistoricalInventoryReplay — acceptance criteria: replay the historical
    failure inventory (hang, lock race, crash, quota misfire, sleep
    timeout) and verify each tags correctly.
  TagFailureClassStamping — tag_failure_class stamps metadata in-place.
  DagExecutorStampsFailureClass — stale-recovery FAILED path stamps it.
  QuotaKeywordSingleSource — QUOTA_KEYWORDS is shared with views._is_quota_failure.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

import time

from django.test import SimpleTestCase, override_settings

from tests.base import APITestCase
from tasks.failure_tagger import (
    FAILURE_CLASSES,
    classify_failure,
    tag_failure_class,
)
from tasks.models import Board, Task, TaskStatus


class ClassifyFailureTaxonomy(SimpleTestCase):
    """One test per taxonomy class with a realistic trace/exit fixture."""

    # ── quota_exhaustion ──────────────────────────────────────────────

    def test_quota_exhaustion_429(self):
        self.assertEqual("quota_exhaustion", classify_failure({
            "last_failure_type": "llm_call_failure",
            "last_failure_reason": "HTTP 429: rate limit exceeded for glm-4.6",
        }))

    def test_quota_exhaustion_usage_limit(self):
        self.assertEqual("quota_exhaustion", classify_failure({
            "last_failure_type": "llm_call_failure",
            "last_failure_reason": "usage limit reached — out of quota",
        }))

    # ── sandbox_unavailable (task #331) ───────────────────────────────

    def test_sandbox_unavailable_pre_execution(self):
        """A sandbox that never booted an agent is its own infra class —
        no agent ran, so it must NOT be tagged crash/silent_hang (which
        would blame or escalate an agent that never executed)."""
        self.assertEqual("sandbox_unavailable", classify_failure({
            "last_failure_type": "sandbox_unavailable",
            "last_failure_reason": "microsandbox: msb daemon not reachable",
        }))

    def test_sandbox_unavailable_is_a_known_class(self):
        self.assertIn("sandbox_unavailable", FAILURE_CLASSES)

    # ── silent_hang ───────────────────────────────────────────────────

    def test_silent_hang_no_output(self):
        self.assertEqual("silent_hang", classify_failure({
            "last_failure_type": "agent_execution_failure",
            "last_failure_reason": (
                "Agent produced no output; likely the CLI crashed or the "
                "model terminated silently before emitting anything."
            ),
        }))

    def test_silent_hang_terminated_silently(self):
        self.assertEqual("silent_hang", classify_failure({
            "last_failure_type": "agent_execution_failure",
            "last_failure_reason": "Process terminated silently before completing.",
        }))

    # ── truncation ────────────────────────────────────────────────────

    def test_truncation_mid_generation(self):
        self.assertEqual("truncation", classify_failure({
            "last_failure_type": "agent_execution_failure",
            "last_failure_reason": "Response truncated at 8192 tokens (output cap hit).",
        }))

    def test_truncation_no_odin_status(self):
        self.assertEqual("truncation", classify_failure({
            "last_failure_type": "agent_execution_failure",
            "last_failure_reason": (
                "Agent did not emit an ODIN-STATUS block. Likely the model "
                "truncated mid-generation or the response terminated silently."
            ),
        }))

    # ── env_missing ───────────────────────────────────────────────────

    def test_env_missing_api_key(self):
        self.assertEqual("env_missing", classify_failure({
            "last_failure_type": "agent_execution_failure",
            "last_failure_reason": "ZAI_API_KEY environment variable is not set",
        }))

    def test_env_missing_backend_auth_401(self):
        self.assertEqual("env_missing", classify_failure({
            "last_failure_type": "backend_auth_failure",
            "last_failure_reason": "Authentication error: TaskIt returned 401 Unauthorized",
        }))

    def test_env_missing_cli_not_found(self):
        self.assertEqual("env_missing", classify_failure({
            "last_failure_type": "cli_not_found",
            "last_failure_reason": "claude not found on path",
        }))

    def test_env_missing_ineligible_tier(self):
        self.assertEqual("env_missing", classify_failure({
            "last_failure_type": "agent_execution_failure",
            "last_failure_reason": "IneligibleTierError: subscription tier not eligible",
        }))

    # ── timeout ───────────────────────────────────────────────────────

    def test_timeout_from_type(self):
        self.assertEqual("timeout", classify_failure({
            "last_failure_type": "timeout",
            "last_failure_reason": "Task execution timed out after 1800s",
        }))

    def test_timeout_from_text(self):
        self.assertEqual("timeout", classify_failure({
            "last_failure_type": "",
            "last_failure_reason": "Command timed out after 300s",
        }))

    # ── stale_execution ───────────────────────────────────────────────

    def test_stale_execution(self):
        self.assertEqual("stale_execution", classify_failure({
            "last_failure_type": "stale_execution",
            "last_failure_reason": "Odin execution process pid=12345 is no longer running",
        }))

    # ── worktree_isolation ────────────────────────────────────────────

    def test_worktree_isolation(self):
        self.assertEqual("worktree_isolation", classify_failure({
            "last_failure_type": "missing_worktree",
            "last_failure_reason": "No task worktree could be created",
        }))

    # ── lock_race ─────────────────────────────────────────────────────

    def test_lock_race(self):
        self.assertEqual("lock_race", classify_failure({
            "last_failure_type": "internal_error",
            "last_failure_reason": "sqlite3.OperationalError: database is locked",
        }))

    # ── transport_error ───────────────────────────────────────────────

    def test_transport_error(self):
        self.assertEqual("transport_error", classify_failure({
            "last_failure_type": "backend_exception",
            "last_failure_reason": "httpx.RemoteProtocolError: peer closed connection",
        }))

    def test_transport_error_connection_reset(self):
        self.assertEqual("transport_error", classify_failure({
            "last_failure_type": "backend_exception",
            "last_failure_reason": "ConnectionResetError: [Errno 104] Connection reset by peer",
        }))

    def test_transport_error_backend_unreachable(self):
        """A backend-unreachable exit (task #339) is a transport-class infra
        failure — it must auto-retry, not crash as an agent failure."""
        self.assertEqual("transport_error", classify_failure({
            "last_failure_type": "agent_execution_failure",
            "last_failure_reason": (
                "Could not reach task backend after 3 attempts. "
                "Backend unreachable: the backend may be restarting"
            ),
        }))

    # ── disk_exhaustion ───────────────────────────────────────────────

    def test_disk_exhaustion(self):
        self.assertEqual("disk_exhaustion", classify_failure({
            "last_failure_type": "internal_error",
            "last_failure_reason": "OSError: [Errno 28] No space left on device",
        }))

    # ── crash ─────────────────────────────────────────────────────────

    def test_crash_exit_code(self):
        self.assertEqual("crash", classify_failure({
            "last_failure_type": "agent_execution_failure",
            "last_failure_reason": "odin exec exited with code 137",
        }))

    def test_crash_internal_error(self):
        self.assertEqual("crash", classify_failure({
            "last_failure_type": "internal_error",
            "last_failure_reason": "RuntimeError: unexpected state transition",
        }))

    def test_crash_backend_exception(self):
        self.assertEqual("crash", classify_failure({
            "last_failure_type": "backend_exception",
            "last_failure_reason": "HTTPStatusError: 500 Server Error",
        }))

    # ── cancelled ─────────────────────────────────────────────────────

    def test_cancelled(self):
        self.assertEqual("cancelled", classify_failure({
            "last_failure_type": "cancelled",
            "last_failure_reason": "Execution stopped by user request",
        }))

    # ── model_unavailable ─────────────────────────────────────────────

    def test_model_unavailable(self):
        self.assertEqual("model_unavailable", classify_failure({
            "last_failure_type": "model_escalation_failure",
            "last_failure_reason": "Model glm-4.6 is not supported",
        }))

    # ── unknown fallback ──────────────────────────────────────────────

    def test_unknown_unmatched_text(self):
        self.assertEqual("unknown", classify_failure({
            "last_failure_type": "",
            "last_failure_reason": "Something completely unexpected happened here",
        }))

    def test_unknown_empty_metadata(self):
        self.assertEqual("unknown", classify_failure({}))

    def test_unknown_no_failure_type(self):
        self.assertEqual("unknown", classify_failure({
            "last_failure_reason": "task failed for unknown reasons",
        }))

    def test_unknown_none_values(self):
        self.assertEqual("unknown", classify_failure({
            "last_failure_type": None,
            "last_failure_reason": None,
        }))

    # ── all return values are in FAILURE_CLASSES ──────────────────────

    def test_all_return_values_in_taxonomy(self):
        """Every possible return is a known failure class."""
        for cls in (
            "quota_exhaustion", "silent_hang", "truncation", "env_missing",
            "timeout", "stale_execution", "worktree_isolation", "lock_race",
            "transport_error", "disk_exhaustion", "crash", "cancelled",
            "model_unavailable", "unknown",
        ):
            self.assertIn(cls, FAILURE_CLASSES)


class HistoricalInventoryReplay(SimpleTestCase):
    """Acceptance: replay the historical failure inventory.

    Each F-number is a documented production failure from
    docs/fable_roadmap/archive/FINDINGS.md.
    """

    def test_f10_silent_hang(self):
        """F10: opencode hung 12m with zero output."""
        self.assertEqual("silent_hang", classify_failure({
            "last_failure_type": "agent_execution_failure",
            "last_failure_reason": (
                "Agent produced no output; likely the CLI crashed or the "
                "model terminated silently before emitting anything."
            ),
        }))

    def test_f31_lock_race(self):
        """F31: opencode SQLite 'database is locked' on cold-start race."""
        self.assertEqual("lock_race", classify_failure({
            "last_failure_type": "internal_error",
            "last_failure_reason": "sqlite3.OperationalError: database is locked",
        }))

    def test_f33_crash(self):
        """F33: Path.cwd() raised after cwd deleted."""
        self.assertEqual("crash", classify_failure({
            "last_failure_type": "internal_error",
            "last_failure_reason": (
                "FileNotFoundError: [Errno 2] No such file or directory"
            ),
        }))

    def test_f45_quota_misfire_not_quota(self):
        """F45: 'None detected.' must NOT be classified as quota_exhaustion."""
        result = classify_failure({
            "last_failure_type": "llm_call_failure",
            "last_failure_reason": "None detected in current execution output.",
        })
        self.assertNotEqual("quota_exhaustion", result)

    def test_f47_sleep_timeout(self):
        """F47: host slept → wall-clock timeout fired on wake."""
        self.assertEqual("timeout", classify_failure({
            "last_failure_type": "timeout",
            "last_failure_reason": "Task execution timed out after 1800s",
        }))


class TagFailureClassStamping(SimpleTestCase):
    """tag_failure_class stamps failure_class into metadata in-place."""

    def test_stamp_sets_key(self):
        metadata = {
            "last_failure_type": "timeout",
            "last_failure_reason": "Task execution timed out",
        }
        cls = tag_failure_class(metadata)
        self.assertEqual(cls, "timeout")
        self.assertEqual(metadata["failure_class"], "timeout")

    def test_stamp_unknown_for_empty(self):
        metadata = {}
        cls = tag_failure_class(metadata)
        self.assertEqual(cls, "unknown")
        self.assertEqual(metadata["failure_class"], "unknown")

    def test_stamp_preserves_existing_keys(self):
        metadata = {
            "last_failure_type": "stale_execution",
            "last_failure_reason": "process died",
            "auto_redispatch_count": 1,
        }
        tag_failure_class(metadata)
        self.assertEqual(metadata["failure_class"], "stale_execution")
        self.assertEqual(metadata["auto_redispatch_count"], 1)


class DagExecutorStampsFailureClass(APITestCase):
    """The stale-recovery FAILED path stamps failure_class on the task."""

    def setUp(self):
        super().setUp()
        self.board = Board.objects.create(name="FC board", working_dir="/tmp/fc")

    def _executing_task(self, active_execution):
        return Task.objects.create(
            board=self.board,
            title="fc task",
            status=TaskStatus.EXECUTING,
            created_by="op@test.com",
            metadata={"active_execution": active_execution},
        )

    def test_stale_recovery_dead_pid_stamps_failure_class(self):
        from tasks.dag_executor import _recover_stale_executions
        task = self._executing_task({"pid": 2 ** 22 + 5, "queued_at": time.time()})
        with override_settings(DAG_EXECUTOR_QUEUED_STALE_SECONDS=1800):
            _recover_stale_executions()
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.metadata["failure_class"], "stale_execution")

    def test_stale_recovery_timeout_stamps_failure_class(self):
        from tasks.dag_executor import _recover_stale_executions
        # No pid (so dead-pid branch doesn't fire), recent queue (so
        # queued-stale branch doesn't fire), but started_at past timeout.
        task = self._executing_task({
            "queued_at": time.time(),
            "started_at": time.time() - 2000,
        })
        with override_settings(
            DAG_EXECUTOR_QUEUED_STALE_SECONDS=1800,
            DAG_EXECUTOR_TASK_TIMEOUT_SECONDS=100,
        ):
            _recover_stale_executions()
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.metadata["failure_class"], "timeout")


class QuotaKeywordsShared(SimpleTestCase):
    """QUOTA_KEYWORDS is a single source of truth shared with views._is_quota_failure."""

    def test_keywords_present(self):
        from tasks.failure_tagger import QUOTA_KEYWORDS
        self.assertIn("429", QUOTA_KEYWORDS)
        self.assertIn("quota", QUOTA_KEYWORDS)

    def test_views_uses_same_list(self):
        from tasks.failure_tagger import QUOTA_KEYWORDS
        from tasks.views import _QUOTA_KEYWORDS
        self.assertEqual(QUOTA_KEYWORDS, _QUOTA_KEYWORDS)
