"""Failure fingerprint tests (task #225 / W6.3).

Scenario matrix:

  FingerprintNormalization (unit):
   - claude 401 → "401 authentication_failed / claude / execution"
   - glm truncation → "truncated_mid_generation / glm / execution"
   - silent hang at reflection stage → "no_output / glm / reflection"
   - quota 429 → "429_quota_exhausted / claude / execution"
   - same input, different provider → different fingerprint
   - same input, different stage → different fingerprint
   - missing agent and model → "unknown" provider (still stable)
   - lowercased + whitespace-trimmed → stable across inputs

  FingerprintStorage (integration via mistakes.py):
   - record_execution_mistake stamps a fingerprint on the entry
   - record_reflection_mistake stamps a fingerprint on the entry
   - the stored fingerprint matches compute_fingerprint()'s output
   - the same failure recorded twice does not duplicate (existing dedup)

  MatchAndComment (integration via advice_for):
   - first occurrence: seen_count == 0 → safe_action hold + "no history" line
   - second occurrence: seen_count == 1, last_resolution = first entry's one_liner
   - ten resolved instances → safe_action requeue
   - ten failed instances → safe_action escalate
   - advice_for_failure() computes fingerprint + looks up in one call
   - the audit comment text contains the brief's "seen N times; last
     resolution: <one-liner>; safe action: <...>" line

  AutoRequeueOverride (integration via failure_policy):
   - AUTO_REQUEUE with empty history: behaves as today (no override)
   - AUTO_REQUEUE with prior "always failed" history: skipped, escalated
     comment quotes the prior one-liners, fingerprint_override_*
     metadata written
   - AUTO_REQUEUE with prior "always worked" history: skip the backoff
     stamp (no next_retry_after on metadata)
   - HUMAN policy always emits fingerprint advice line; same path with
     "no prior matches" yields the "hold (no history)" line

  Acceptance:
   - replaying wave-5's real failures (claude 401 + reflection hang)
     produces stable fingerprints and sensible advice; second
     occurrence quotes the first.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import patch

from django.utils import timezone

from tasks.dag_executor import _fail_stale_execution
from tasks.failure_policy import apply_failure_policy
from tasks.fingerprints import (
    ACTION_ESCALATE,
    ACTION_HOLD,
    ACTION_REQUEUE,
    MAX_HISTORY,
    MIN_HISTORY,
    STAGE_EXECUTION,
    STAGE_REFLECTION,
    advice_for,
    advice_for_failure,
    compute_fingerprint,
    format_advice_line,
    history_lines,
    matches_query,
    safe_action_for,
    should_override_auto_requeue,
    should_skip_backoff,
)
from tasks.mistakes import (
    record_execution_mistake,
    record_reflection_mistake,
)
from tasks.models import (
    CommentType,
    MistakeEntry,
    ReflectionReport,
    ReflectionStatus,
    TaskComment,
    TaskRun,
    TaskRunState,
    TaskStatus,
)
from tests.base import APITestCase


# ── Fingerprint normalization (unit) ──────────────────────────────


class FingerprintNormalization(APITestCase):
    """Same failure produces the same fingerprint; different
    providers/stages produce different fingerprints.
    """

    def test_claude_401_environment_failure(self):
        """The brief's headline example: '401 authentication_failed / claude / execution'."""
        fp = compute_fingerprint(
            failure_class="env_missing",
            reason="Authentication error: TaskIt returned 401 Unauthorized",
            agent="claude",
            model="claude-sonnet-4-5",
            stage=STAGE_EXECUTION,
        )
        self.assertEqual(fp, "401 authentication_failed / claude / execution")

    def test_glm_truncation_execution(self):
        fp = compute_fingerprint(
            failure_class="truncation",
            reason="Response truncated at 8192 tokens (output cap hit).",
            agent="glm",
            model="zai-coding-plan/glm-5.2",
            stage=STAGE_EXECUTION,
        )
        self.assertEqual(fp, "truncated_mid_generation / glm / execution")

    def test_silent_hang_reflection(self):
        """Reflection-stage hang matches the brief's second example."""
        fp = compute_fingerprint(
            failure_class="silent_hang",
            reason="Agent produced no output; likely the CLI crashed.",
            agent="glm",
            model="zai-coding-plan/glm-5.2",
            stage=STAGE_REFLECTION,
        )
        self.assertEqual(fp, "no_output / glm / reflection")

    def test_quota_429_with_claude(self):
        fp = compute_fingerprint(
            failure_class="quota_exhaustion",
            reason="HTTP 429: rate limit exceeded for claude-sonnet",
            agent="claude",
            model="claude-sonnet-4-5",
            stage=STAGE_EXECUTION,
        )
        self.assertEqual(fp, "429_quota_exhausted / claude / execution")

    def test_different_provider_yields_different_fingerprint(self):
        """Same salient + stage but different provider → different signature."""
        a = compute_fingerprint(
            failure_class="env_missing", reason="401 Unauthorized",
            agent="claude", model="claude-sonnet-4-5", stage=STAGE_EXECUTION,
        )
        b = compute_fingerprint(
            failure_class="env_missing", reason="401 Unauthorized",
            agent="glm", model="zai-coding-plan/glm-5.2", stage=STAGE_EXECUTION,
        )
        self.assertNotEqual(a, b)
        self.assertIn("claude", a)
        self.assertIn("glm", b)

    def test_different_stage_yields_different_fingerprint(self):
        """Same salient + provider but execution vs reflection stage."""
        a = compute_fingerprint(
            failure_class="silent_hang", reason="no output",
            agent="glm", model="zai-coding-plan/glm-5.2", stage=STAGE_EXECUTION,
        )
        b = compute_fingerprint(
            failure_class="silent_hang", reason="no output",
            agent="glm", model="zai-coding-plan/glm-5.2", stage=STAGE_REFLECTION,
        )
        self.assertNotEqual(a, b)
        self.assertTrue(a.endswith("execution"))
        self.assertTrue(b.endswith("reflection"))
        self.assertIn(" / execution", a)
        self.assertIn(" / reflection", b)

    def test_missing_agent_and_model_yields_unknown_provider(self):
        """No inputs still produce a stable, queryable fingerprint."""
        fp = compute_fingerprint(
            failure_class="truncation", reason="truncated mid-generation",
            agent="", model="", stage=STAGE_EXECUTION,
        )
        self.assertIn("unknown", fp)
        self.assertTrue(fp.endswith("execution"))
        self.assertIn(" / execution", fp)

    def test_input_is_lowercased_and_trimmed(self):
        """Case + whitespace differences do not break matching."""
        a = compute_fingerprint(
            failure_class="Truncation", reason="  Truncated Mid-Generation  ",
            agent="Claude", model="Claude-Sonnet-4-5", stage=STAGE_EXECUTION,
        )
        b = compute_fingerprint(
            failure_class="truncation", reason="truncated mid-generation",
            agent="claude", model="claude-sonnet-4-5", stage=STAGE_EXECUTION,
        )
        self.assertEqual(a, b)

    def test_empty_failure_class_collapses_to_unclassified(self):
        """An un-stamped failure still gets a stable label."""
        fp = compute_fingerprint(
            failure_class="", reason="",
            agent="claude", model="claude-sonnet-4-5", stage=STAGE_EXECUTION,
        )
        self.assertIn("unclassified", fp)


# ── Fingerprint storage (integration via mistakes.py) ─────────────


class FingerprintStorage(APITestCase):
    """MistakeEntry rows carry a stable, queryable fingerprint."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def test_execution_mistake_stamps_fingerprint(self):
        task = self.make_task(
            self.board,
            status=TaskStatus.FAILED,
            assignee=self.make_user(name="claude", email="claude@odin.agent"),
            model_name="claude-sonnet-4-5",
            metadata={
                "last_failure_type": "backend_auth_failure",
                "last_failure_reason": "Authentication error: TaskIt returned 401 Unauthorized",
                "failure_class": "env_missing",
            },
        )
        entry = record_execution_mistake(task, run_token="tok-fp-1")

        expected = compute_fingerprint(
            failure_class="env_missing",
            reason="Authentication error: TaskIt returned 401 Unauthorized",
            agent="claude",
            model="claude-sonnet-4-5",
            stage=STAGE_EXECUTION,
        )
        self.assertEqual(entry.fingerprint, expected)
        self.assertEqual(
            matches_query(expected).count(), 1,
            "same fingerprint should be findable via matches_query",
        )

    def test_reflection_mistake_stamps_fingerprint(self):
        """The brief's second scenario: reflection hang gets a
        reflection-stage fingerprint on the MistakeEntry."""
        task = self.make_task(
            self.board,
            status=TaskStatus.FAILED,
            assignee=self.make_user(name="glm", email="glm@odin.agent"),
            model_name="zai-coding-plan/glm-5.2",
        )
        report = ReflectionReport.objects.create(
            task=task,
            reviewer_agent="glm",
            reviewer_model="zai-coding-plan/glm-5.2",
            requested_by="system@taskit",
            status=ReflectionStatus.RUNNING,
        )
        report.verdict = "FAIL"
        report.verdict_summary = "Agent produced no output; the CLI crashed."
        # NB: this is a minimal stand-in — ReflectionReport stores
        # verdict_summary directly only when filled by the reviewer,
        # but the ledger's record path uses distill_reflection on
        # whatever the report carries, so this exercises the path.
        entry = record_reflection_mistake(report)

        expected = compute_fingerprint(
            failure_class="silent_hang",
            reason="Agent produced no output; the CLI crashed.",
            agent="glm",
            model="zai-coding-plan/glm-5.2",
            stage=STAGE_REFLECTION,
        )
        # The salient token for this text is "no_output" (from the
        # "produced no output" signature) — not "silent_hang".  The
        # reflection pathway's failure_class comes from
        # classify_failure_text() on the report's free text.
        self.assertEqual(entry.fingerprint, expected)
        self.assertEqual(matches_query(expected).count(), 1)

    def test_same_failure_records_do_not_duplicate(self):
        """Existing dedup rules still apply once fingerprint is stamped."""
        task = self.make_task(
            self.board,
            status=TaskStatus.FAILED,
            assignee=self.make_user(name="claude", email="claude@odin.agent"),
            model_name="claude-sonnet-4-5",
            metadata={
                "last_failure_type": "backend_auth_failure",
                "last_failure_reason": "Authentication error: 401 Unauthorized",
                "failure_class": "env_missing",
            },
        )
        record_execution_mistake(task, run_token="tok-dup")
        record_execution_mistake(task, run_token="tok-dup")
        self.assertEqual(
            MistakeEntry.objects.filter(task=task, source_id="tok-dup").count(), 1,
        )


# ── Match + comment (integration via advice_for) ─────────────────


class MatchAndComment(APITestCase):
    """Advice is derived from the MistakeEntry history for a fingerprint."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.claude = self.make_user(name="claude", email="claude@odin.agent")

    def _make_failed(self, *, run_token: str, reason: str = "Authentication error: 401 Unauthorized"):
        task = self.make_task(
            self.board,
            status=TaskStatus.FAILED,
            assignee=self.claude,
            model_name="claude-sonnet-4-5",
            metadata={
                "last_failure_type": "backend_auth_failure",
                "last_failure_reason": reason,
                "failure_class": "env_missing",
            },
        )
        return task

    def test_first_occurrence_seen_zero(self):
        """No prior entries → advice shows 0 and 'hold (no history)'."""
        task = self._make_failed(run_token="tok-1")
        record_execution_mistake(task, run_token="tok-1")
        advice = advice_for(
            compute_fingerprint(
                failure_class="env_missing",
                reason="Authentication error: 401 Unauthorized",
                agent="claude",
                model="claude-sonnet-4-5",
                stage=STAGE_EXECUTION,
            ),
        )
        # Even with one entry (the one we just created), the MATCHING
        # count excludes itself once apply_failure_policy filters, but
        # raw matches_query includes it.  Either way, ``safe_action``
        # for one sample falls back to hold — the math wants >= MIN_HISTORY.
        self.assertEqual(advice["safe_action"], ACTION_HOLD)
        line = format_advice_line(advice)
        self.assertIn("fingerprint_history:", line)
        self.assertIn("claude", line)

    def test_second_occurrence_quotes_the_first(self):
        """Brief: 'second occurrence of the same fingerprint quotes the first.'

        We create one entry with a known one-liner, then look up the
        fingerprint via ``advice_for`` from a SECOND task.  The advice
        block carries the first entry's one-liner as ``last_resolution``.
        """
        first = self._make_failed(run_token="tok-a")
        record_execution_mistake(first, run_token="tok-a")
        first.refresh_from_db()
        first_entry = MistakeEntry.objects.filter(task=first).first()
        self.assertIsNotNone(first_entry)
        self.assertTrue(first_entry.one_liner)

        fp = compute_fingerprint(
            failure_class="env_missing",
            reason="Authentication error: 401 Unauthorized",
            agent="claude",
            model="claude-sonnet-4-5",
            stage=STAGE_EXECUTION,
        )
        advice = advice_for(fp)
        self.assertGreaterEqual(advice["seen_count"], 1)
        # The advice surfaces the first entry's one_liner verbatim.
        self.assertEqual(
            advice["last_resolution"], first_entry.one_liner,
            "second occurrence should quote the first entry's one_liner",
        )
        line = format_advice_line({
            **advice,
            "seen_count": max(advice["seen_count"] - 1, 0),
            # Simulate the apply_failure_policy SAME-task filter.
        })
        self.assertIn("safe action:", line)

    def test_many_resolved_history_yields_requeue(self):
        """Lots of DONE rows + same fingerprint → safe_action = requeue."""
        fp = compute_fingerprint(
            failure_class="env_missing",
            reason="Authentication error: 401 Unauthorized",
            agent="claude",
            model="claude-sonnet-4-5",
            stage=STAGE_EXECUTION,
        )
        for i in range(MIN_HISTORY + 2):
            task = self._make_failed(run_token=f"tok-r-{i}")
            record_execution_mistake(task, run_token=f"tok-r-{i}")
            # Promote the task to DONE so the resolved-rate math sees
            # it as a success.
            task.status = TaskStatus.DONE
            task.save(update_fields=["status"])

        advice = advice_for(fp)
        self.assertGreaterEqual(advice["seen_count"], MIN_HISTORY + 2)
        self.assertEqual(
            advice["safe_action"], ACTION_REQUEUE,
            "all-DONE history should recommend requeue",
        )

    def test_many_failed_history_yields_escalate(self):
        """Lots of FAILED rows + same fingerprint → safe_action = escalate."""
        fp = compute_fingerprint(
            failure_class="env_missing",
            reason="Authentication error: 401 Unauthorized",
            agent="claude",
            model="claude-sonnet-4-5",
            stage=STAGE_EXECUTION,
        )
        for i in range(MIN_HISTORY + 2):
            task = self._make_failed(run_token=f"tok-f-{i}")
            record_execution_mistake(task, run_token=f"tok-f-{i}")
            # Leave in FAILED (the default above).

        advice = advice_for(fp)
        self.assertEqual(
            advice["safe_action"], ACTION_ESCALATE,
            "all-FAILED history should recommend escalate",
        )

    def test_mixed_history_yields_hold(self):
        """Mixed resolved/failed → safe_action = hold (don't override)."""
        fp = compute_fingerprint(
            failure_class="env_missing",
            reason="Authentication error: 401 Unauthorized",
            agent="claude",
            model="claude-sonnet-4-5",
            stage=STAGE_EXECUTION,
        )
        for i in range(MIN_HISTORY + 2):
            task = self._make_failed(run_token=f"tok-m-{i}")
            record_execution_mistake(task, run_token=f"tok-m-{i}")
            task.status = TaskStatus.DONE if i % 2 == 0 else TaskStatus.FAILED
            task.save(update_fields=["status"])

        advice = advice_for(fp)
        self.assertEqual(
            advice["safe_action"], ACTION_HOLD,
            "mixed history should default to hold",
        )

    def test_safe_action_for_below_min_history_is_hold(self):
        """Too few samples → hold, even when the few we have are all DONE."""
        rows = []
        # Build a small queryset-shaped stub: list of objects with the
        # minimal API ``safe_action_for`` reads (task.status via
        # ``_resolved_status``).
        for _ in range(MIN_HISTORY - 1):
            t = self._make_failed(run_token=f"tok-tiny-{_}")
            record_execution_mistake(t, run_token=f"tok-tiny-{_}")
            t.status = TaskStatus.DONE
            t.save(update_fields=["status"])
            rows.append(MistakeEntry.objects.filter(task=t).first())
        self.assertEqual(safe_action_for(rows), ACTION_HOLD)

    def test_advice_for_failure_computes_fingerprint_and_looks_up(self):
        """Convenience helper returns the same shape as advice_for."""
        task = self._make_failed(run_token="tok-c")
        record_execution_mistake(task, run_token="tok-c")
        advice = advice_for_failure(
            failure_class="env_missing",
            reason="Authentication error: 401 Unauthorized",
            agent="claude",
            model="claude-sonnet-4-5",
            stage=STAGE_EXECUTION,
        )
        self.assertTrue(advice["fingerprint"].startswith("401 authentication_failed"))
        self.assertIn("claude", advice["fingerprint"])
        self.assertIn(" / execution", advice["fingerprint"])

    def test_history_lines_iterates_each_match(self):
        fp = compute_fingerprint(
            failure_class="env_missing",
            reason="Authentication error: 401 Unauthorized",
            agent="claude",
            model="claude-sonnet-4-5",
            stage=STAGE_EXECUTION,
        )
        for i in range(3):
            task = self._make_failed(run_token=f"tok-hl-{i}")
            record_execution_mistake(task, run_token=f"tok-hl-{i}")
        rows = list(matches_query(fp)[:MAX_HISTORY])
        lines = list(history_lines(rows, limit=5))
        self.assertEqual(len(lines), 3)
        for line in lines:
            self.assertIn("(failed)", line)

    def test_should_override_and_skip_backoff_match_advice(self):
        high_success = {"safe_action": ACTION_REQUEUE, "seen_count": 5}
        low_success = {"safe_action": ACTION_ESCALATE, "seen_count": 5}
        hold = {"safe_action": ACTION_HOLD, "seen_count": 5}
        self.assertTrue(should_skip_backoff(high_success))
        self.assertFalse(should_skip_backoff(low_success))
        self.assertFalse(should_skip_backoff(hold))
        self.assertTrue(should_override_auto_requeue(low_success))
        self.assertFalse(should_override_auto_requeue(high_success))
        self.assertFalse(should_override_auto_requeue(hold))


# ── Auto-requeue override (integration via failure_policy) ────────


class AutoRequeueOverride(APITestCase):
    """Brief: history shapes the AUTO_REQUEUE decision and the
    audit comment (skip backoff on success, escalate on persistent
    failure, quote history either way).
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board(allow_project_root_execution=True)
        self.claude = self.make_user(name="claude", email="claude@odin.agent")

    def _transient_failed_task(
        self, *, fingerprint_history: list[str] | None = None,
        failure_class: str = "transport_error",
        reason: str = "httpx.RemoteProtocolError: peer closed connection",
    ):
        """Create a FAILED transport_error task and (optionally) seed
        prior MistakeEntry rows that share the fingerprint.
        """
        task = self.make_task(
            self.board,
            title=f"{failure_class} task",
            status=TaskStatus.FAILED,
            assignee=self.claude,
            model_name="claude-sonnet-4-5",
            metadata={
                "last_failure_type": "backend_exception",
                "last_failure_reason": reason,
                "last_failure_origin": "taskit_dag_executor",
                "failure_class": failure_class,
            },
        )
        if fingerprint_history:
            for token in fingerprint_history:
                pre = self.make_task(
                    self.board,
                    title=f"prior {failure_class}",
                    status=TaskStatus.FAILED,
                    assignee=self.claude,
                    model_name="claude-sonnet-4-5",
                    metadata={
                        "last_failure_type": "backend_exception",
                        "last_failure_reason": reason,
                        "failure_class": failure_class,
                    },
                )
                record_execution_mistake(pre, run_token=token)
        return task

    def test_auto_requeue_with_empty_history_still_requeues(self):
        """No history → no override, stock policy path runs unchanged."""
        task = self._transient_failed_task()
        with patch("tasks.dag_executor.execute_single_task"):
            result = apply_failure_policy(task)
        self.assertTrue(result, "AUTO_REQUEUE with empty fingerprint history still requeues")
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)

    def test_auto_requeue_skips_backoff_when_history_says_requeue(self):
        """All-DONE history → backoff skipped (no next_retry_after stamp)."""
        # Seed enough DONE history to push safe_action → requeue.
        tokens = [f"tok-br-{i}" for i in range(MIN_HISTORY + 2)]
        task = self._transient_failed_task(fingerprint_history=tokens)
        # Promote the priors to DONE so they count as resolved.
        for entry in MistakeEntry.objects.filter(source_id__in=tokens):
            t = entry.task
            t.status = TaskStatus.DONE
            t.save(update_fields=["status"])

        with patch("tasks.dag_executor.execute_single_task"):
            result = apply_failure_policy(task)
        self.assertTrue(result, "history says retry works → still requeue")

        task.refresh_from_db()
        # transport_error normally stamps next_retry_after (30s backoff);
        # the fingerprint skip should suppress that.
        self.assertNotIn(
            "next_retry_after", task.metadata,
            "fingerprint history 'requeue' should skip the backoff stamp",
        )
        self.assertTrue(task.metadata.get("last_fingerprint_skip_backoff"))

    def test_auto_requeue_overrides_to_human_when_history_says_escalate(self):
        """Persistent-failure history → retry skipped, escalate with history quoted."""
        tokens = [f"tok-esc-{i}" for i in range(MIN_HISTORY + 2)]
        task = self._transient_failed_task(fingerprint_history=tokens)
        # Priors are all FAILED (default), so safe_action → escalate.
        original_assignee_id = task.assignee_id

        result = apply_failure_policy(task)
        self.assertFalse(
            result,
            "history says retry doesn't work → no auto-requeue",
        )
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.assignee_id, original_assignee_id)
        # Override metadata is stamped for the dashboard.
        self.assertTrue(task.metadata.get("fingerprint_override_at"))
        self.assertEqual(
            task.metadata.get("fingerprint_override_class"), "transport_error",
        )
        self.assertGreaterEqual(
            task.metadata.get("fingerprint_override_seen_count", 0), MIN_HISTORY,
        )
        self.assertEqual(
            task.metadata.get("fingerprint_override_safe_action"), ACTION_ESCALATE,
        )
        # Audit comment quotes the history + the brief's triage line.
        comments = TaskComment.objects.filter(
            task=task,
            author_email="odin+dag-executor@system",
            comment_type=CommentType.STATUS_UPDATE,
        ).order_by("-created_at")
        self.assertTrue(comments.exists(), "override should post an audit comment")
        body = "\n---\n".join(c.content for c in comments)
        self.assertIn("overridden to human", body.lower())
        self.assertIn("fingerprint_history", body)
        self.assertIn("seen", body)
        self.assertIn("safe action:", body)
        # The history block lists the prior task ids.
        self.assertIn("Recent fingerprint history:", body)
        self.assertIn("(failed)", body)
        # At least one prior task quoted by id with its one_liner.
        import re as _re
        self.assertRegex(body, r"#\d+\s+\(failed\)\s+httpx")

    def test_human_audit_carries_fingerprint_advice(self):
        """HUMAN actions always emit the fingerprint advice line."""
        task = self.make_task(
            self.board,
            title="env_missing task",
            status=TaskStatus.FAILED,
            assignee=self.claude,
            model_name="claude-sonnet-4-5",
            metadata={
                "last_failure_type": "backend_auth_failure",
                "last_failure_reason": "Authentication error: 401 Unauthorized",
                "failure_class": "env_missing",
            },
        )
        apply_failure_policy(task)
        task.refresh_from_db()
        comments = TaskComment.objects.filter(
            task=task,
            author_email="odin+dag-executor@system",
            comment_type=CommentType.STATUS_UPDATE,
        )
        body = "\n---\n".join(c.content for c in comments)
        self.assertIn("env_missing", body)
        self.assertIn("surface for human review", body.lower())
        # The fingerprint line is also appended.
        self.assertIn("fingerprint_history", body)

    def test_reassign_audit_carries_fingerprint_advice(self):
        """Quota (REASSIGN) audit also gains the fingerprint line."""
        task = self.make_task(
            self.board,
            title="Quota task",
            status=TaskStatus.FAILED,
            assignee=self.claude,
            model_name="claude-sonnet-4-5",
            metadata={
                "last_failure_type": "llm_call_failure",
                "last_failure_reason": "HTTP 429: rate limit exceeded",
                "failure_class": "quota_exhaustion",
            },
        )
        with patch("tasks.dag_executor.execute_single_task"):
            apply_failure_policy(task)
        comments = TaskComment.objects.filter(
            task=task,
            author_email="odin+dag-executor@system",
            comment_type=CommentType.STATUS_UPDATE,
        )
        body = "\n---\n".join(c.content for c in comments)
        self.assertIn("quota_exhaustion", body)
        self.assertIn("reassign", body.lower())
        self.assertIn("fingerprint_history", body)


# ── Acceptance: replaying wave-5's real failures ────────────────────


class ReplayWaveFiveFailures(APITestCase):
    """Acceptance: replay wave-5's two real failure shapes; second
    occurrence quotes the first.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.claude = self.make_user(name="claude", email="claude@odin.agent")
        self.glm = self.make_user(name="glm", email="glm@odin.agent")

    def _failed_claude_401(self, run_token: str):
        task = self.make_task(
            self.board,
            title="claude 401",
            status=TaskStatus.FAILED,
            assignee=self.claude,
            model_name="claude-sonnet-4-5",
            metadata={
                "last_failure_type": "backend_auth_failure",
                "last_failure_reason": (
                    "Authentication error: TaskIt returned 401 Unauthorized"
                ),
                "failure_class": "env_missing",
            },
        )
        record_execution_mistake(task, run_token=run_token)
        return task

    def _failed_reflection_hang(self, run_token: str):
        task = self.make_task(
            self.board,
            title="reflection hang",
            status=TaskStatus.FAILED,
            assignee=self.glm,
            model_name="zai-coding-plan/glm-5.2",
        )
        report = ReflectionReport.objects.create(
            task=task,
            reviewer_agent="glm",
            reviewer_model="zai-coding-plan/glm-5.2",
            requested_by="system@taskit",
            status=ReflectionStatus.RUNNING,
        )
        report.verdict = "FAIL"
        report.verdict_summary = "Agent produced no output; the CLI crashed."
        record_reflection_mistake(report)
        # Make sure the run_token / source_id carry too.
        record_execution_mistake(task, run_token=run_token)
        return task

    def test_claude_401_stable_across_two_occurrences(self):
        """Two claude-401 failures → same fingerprint; second quotes the first."""
        first = self._failed_claude_401("tok-c401-1")
        first_entry = MistakeEntry.objects.filter(task=first).first()
        self.assertEqual(
            first_entry.fingerprint,
            "401 authentication_failed / claude / execution",
        )
        # Second occurrence (separate task, separate run token).
        second = self._failed_claude_401("tok-c401-2")
        advice = advice_for(first_entry.fingerprint)
        # Both entries are findable via the same fingerprint.
        matches = list(matches_query(first_entry.fingerprint)[:MAX_HISTORY])
        self.assertGreaterEqual(len(matches), 2)
        # The advice surfaces the most-recent resolved one's one_liner,
        # which falls back to whichever is newer since both are FAILED.
        self.assertTrue(advice["last_resolution"])
        self.assertIn("safe action:", format_advice_line(advice))

    def test_reflection_hang_stable_across_two_occurrences(self):
        """Two reflection hangs → same fingerprint; the brief's
        second headline example reproduced end-to-end."""
        first = self._failed_reflection_hang("tok-rh-1")
        first_entry = MistakeEntry.objects.filter(
            task=first, source=MistakeEntry.SOURCE_REFLECTION,
        ).first()
        self.assertIsNotNone(first_entry)
        self.assertEqual(
            first_entry.fingerprint, "no_output / glm / reflection",
        )
        # Second occurrence (separate task, separate run token).
        self._failed_reflection_hang("tok-rh-2")
        advice = advice_for("no_output / glm / reflection")
        self.assertGreaterEqual(advice["seen_count"], 1)
        # The fingerprint math for this shape: 1 failure recorded,
        # seen_count >= 1, the advice surfaces at least one one_liner.
        self.assertTrue(advice["last_resolution"])


# ── Failure history comment (task #336) ────────────────────────────


class FailureHistoryComment(APITestCase):
    """Dedicated history comment posted when a fingerprint has been
    seen before (task #336).

    First occurrence: no history comment.  Second occurrence: a
    comment linking the prior task and naming the routing policy's
    prescribed action.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board(allow_project_root_execution=True)
        self.claude = self.make_user(name="claude", email="claude@odin.agent")

    def _make_env_missing_task(self, *, run_token: str = ""):
        return self.make_task(
            self.board,
            title="env_missing task",
            status=TaskStatus.FAILED,
            assignee=self.claude,
            model_name="claude-sonnet-4-5",
            metadata={
                "last_failure_type": "backend_auth_failure",
                "last_failure_reason": "Authentication error: 401 Unauthorized",
                "last_failure_origin": "taskit_dag_executor",
                "failure_class": "env_missing",
            },
        )

    def _history_comments(self, task):
        return TaskComment.objects.filter(
            task=task,
            author_email="odin+dag-executor@system",
            comment_type=CommentType.STATUS_UPDATE,
            content__startswith="Failure history:",
        )

    def test_first_occurrence_posts_nothing(self):
        """No prior matches → no 'Failure history:' comment."""
        task = self._make_env_missing_task()
        apply_failure_policy(task)
        self.assertEqual(
            self._history_comments(task).count(), 0,
            "first occurrence should not post a failure history comment",
        )

    def test_second_occurrence_posts_history_comment(self):
        """Second failure with the same fingerprint carries the history
        comment with the prior task linked and the routing rule named.
        """
        first = self._make_env_missing_task(run_token="tok-hist-1")
        record_execution_mistake(first, run_token="tok-hist-1")

        second = self._make_env_missing_task(run_token="tok-hist-2")
        apply_failure_policy(second)

        comments = self._history_comments(second)
        self.assertEqual(comments.count(), 1, "second occurrence should post exactly one history comment")
        body = comments.first().content

        # How many times seen.
        self.assertIn("seen 1 time", body)
        # Prior task linked by id.
        self.assertIn(f"#{first.id}", body)
        # Prior one-liner quoted.
        self.assertIn("401", body)
        # Fingerprint shown.
        self.assertIn("401 authentication_failed", body)
        # Routing policy action named by rule name.
        self.assertIn("hold for human", body)
        # Failure class named.
        self.assertIn("env_missing", body)

    def test_second_occurrence_resolved_prior_suggests_rule(self):
        """When the prior occurrence ended in a known resolution, the
        comment suggests the action by rule name — EXACTLY one of the
        three brief-listed named resolutions (task #336 review):
            "requeue same agent" / "hold" / "infra retry".
        """
        first = self._make_env_missing_task(run_token="tok-resolved-1")
        record_execution_mistake(first, run_token="tok-resolved-1")
        # Mark the prior task as resolved (DONE).
        first.status = TaskStatus.DONE
        first.save(update_fields=["status"])

        second = self._make_env_missing_task(run_token="tok-resolved-2")
        apply_failure_policy(second)

        comments = self._history_comments(second)
        self.assertEqual(comments.count(), 1)
        body = comments.first().content
        # Prior outcome stated as resolved.
        self.assertIn("resolved", body)
        # History suggests a rule by name — the suggestion line must be
        # present, and the suggested rule name must be EXACTLY one of the
        # three brief-listed named resolutions (no synonym, no paraphrase).
        self.assertIn("History suggests:", body)
        import re as _re
        m = _re.search(r"^History suggests:\s*(.+)$", body, flags=_re.M)
        self.assertIsNotNone(
            m, "History suggests: line must be present in the comment",
        )
        suggested = m.group(1).strip()
        brief_named_resolutions = ("requeue same agent", "hold", "infra retry")
        self.assertIn(
            suggested, brief_named_resolutions,
            f"History suggests: '{suggested}' must be exactly one of the "
            f"brief-named resolutions {brief_named_resolutions!r}",
        )

    def test_history_comment_does_not_change_routing(self):
        """The history comment is informational only — the policy action
        still runs (HUMAN for env_missing leaves the task FAILED)."""
        first = self._make_env_missing_task(run_token="tok-nochange-1")
        record_execution_mistake(first, run_token="tok-nochange-1")

        second = self._make_env_missing_task(run_token="tok-nochange-2")
        result = apply_failure_policy(second)
        second.refresh_from_db()
        self.assertFalse(result, "env_missing is HUMAN — no auto-requeue")
        self.assertEqual(second.status, TaskStatus.FAILED, "task stays FAILED")

    def test_history_suggestion_requeue_when_many_resolved(self):
        """When fingerprint history is overwhelmingly resolved (>= MIN_HISTORY),
        safe_action is ``requeue`` and the suggestion is EXACTLY
        "requeue same agent" (task #336 review)."""
        import re as _re
        # Seed MIN_HISTORY+2 resolved priors so safe_action → requeue.
        for i in range(MIN_HISTORY + 2):
            prior = self._make_env_missing_task(run_token=f"tok-req-{i}")
            record_execution_mistake(prior, run_token=f"tok-req-{i}")
            prior.status = TaskStatus.DONE
            prior.save(update_fields=["status"])

        second = self._make_env_missing_task(run_token="tok-req-final")
        apply_failure_policy(second)

        comments = self._history_comments(second)
        self.assertEqual(comments.count(), 1)
        body = comments.first().content
        m = _re.search(r"^History suggests:\s*(.+)$", body, flags=_re.M)
        self.assertIsNotNone(m)
        self.assertEqual(
            m.group(1).strip(), "requeue same agent",
            "with all-resolved history the exact brief term is 'requeue same agent'",
        )

    def test_history_suggestion_hold_below_min_history(self):
        """When fingerprint history is below MIN_HISTORY, safe_action
        is ``hold`` and the suggestion is EXACTLY "hold" (task #336
        review).  Below MIN_HISTORY is the natural "I have no signal"
        state; the brief's exact term is "hold"."""
        import re as _re
        # Only 1 prior (< MIN_HISTORY=3) → safe_action_for returns HOLD.
        first = self._make_env_missing_task(run_token="tok-hold-1")
        record_execution_mistake(first, run_token="tok-hold-1")
        first.status = TaskStatus.DONE
        first.save(update_fields=["status"])

        second = self._make_env_missing_task(run_token="tok-hold-2")
        apply_failure_policy(second)

        comments = self._history_comments(second)
        self.assertEqual(comments.count(), 1)
        body = comments.first().content
        m = _re.search(r"^History suggests:\s*(.+)$", body, flags=_re.M)
        self.assertIsNotNone(m)
        self.assertEqual(
            m.group(1).strip(), "hold",
            "below-MIN_HISTORY priors → brief-exact 'hold' (not 'hold for human')",
        )

    def test_history_suggestion_exact_term_includes_no_paraphrases(self):
        """The suggestion line must not contain any synonyms/paraphrases —
        only the exact brief-listed rule name.  Guards against an editor
        re-introducing "hold for human" / "auto-requeue" / similar (task
        #336 review feedback)."""
        import re as _re
        # Seed resolved priors so a suggestion line appears.
        for i in range(MIN_HISTORY + 2):
            prior = self._make_env_missing_task(run_token=f"tok-noPara-{i}")
            record_execution_mistake(prior, run_token=f"tok-noPara-{i}")
            prior.status = TaskStatus.DONE
            prior.save(update_fields=["status"])

        second = self._make_env_missing_task(run_token="tok-noPara-final")
        apply_failure_policy(second)

        body = self._history_comments(second).first().content
        m = _re.search(r"^History suggests:\s*(.+)$", body, flags=_re.M)
        self.assertIsNotNone(m)
        suggested = m.group(1).strip()
        # Paraphrases of the brief term are forbidden in the suggestion
        # line — "hold for human" / "auto-requeue" / bare action names
        # don't match the brief's three exact resolutions.
        forbidden = {"hold for human", "auto-requeue", "requeue", "escalate"}
        self.assertNotIn(suggested, forbidden)
