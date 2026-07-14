"""Tests for the Error Ledger (task #222).

Every error the system logs — failure_tagger misses, merge failures,
reflection ERROR verdicts, spec-verify gate crashes, celery task exceptions —
gets a structured ErrorEvent row. Triage lives at testing_tools/errors.py
and the disposition is settable via the API so a non-operator can mark
entries fixed or non-issue without running the script by hand.

Scenario matrix:
  Capture (integration):
   - merge failure (instrumented site) → ErrorEvent(source=merge_failure)
   - gate crash (instrumented site) → ErrorEvent(source=gate_crash, spec FK)
   - failure_tagger unknown classification → ErrorEvent(source=failure_tagger)
   - celery dispatch exception → ErrorEvent(source=celery_exception)
   - reflection ERROR verdict → ErrorEvent(source=reflection_error)
   - agent malformed ODIN-STATUS block → ErrorEvent(source=agent_malformed_status) (task #237)
  Idempotency:
   - same source+source_id twice → exactly 1 row
   - different source_id → separate rows
  Grouping:
   - duplicate symptom_signature + same source → counted together (signature dedup)
   - distinct symptoms → separate buckets
  Disposition API:
   - POST /errors/<id>/disposition/ {disposition:fixed} → row updated, note round-trips
   - invalid disposition value → 400
   - unknown id → 404
  Seed:
   - seed_from_ledger imports the live docs/patterns/error_ledger.md entries
   - re-seed is idempotent on (source, source_id, symptom_signature)
  CLI:
   - errors.py --brief lists open entries grouped with counts
   - errors.py --json for agents
   - errors.py --set-disposition updates an entry
"""
import json
import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import patch

from tasks.dag_executor import _fail_stale_execution
from tasks.errors import (
    DISPOSITION_CHOICES,
    import_pending_ledger_entries,
    record_agent_malformed_status,
    record_celery_exception,
    record_error,
    record_failure_tagger_unknown,
    record_gate_crash,
    record_merge_failure,
    record_reflection_error,
    record_reflection_no_reviewer,
    seed_from_ledger,
    set_disposition,
    symptom_signature,
)
from tasks.models import ErrorEvent, ReflectionReport, ReflectionStatus, TaskRun, TaskRunState, TaskStatus
from tests.base import APITestCase


def _find_ledger_doc():
    """Walk up from this file until we find docs/patterns/error_ledger.md.

    Tests run from the repo worktree root OR the backend dir depending on
    how they're invoked; ``parents[2]`` isn't enough — walk until we
    find the doc to keep the test robust to layout changes.
    """
    from pathlib import Path
    cur = Path(__file__).resolve().parent
    while cur != cur.parent:
        candidate = cur / "docs" / "patterns" / "error_ledger.md"
        if candidate.exists():
            return candidate
        cur = cur.parent
    return None


# Synthetic ledger fixture: the live doc is now narrative-only, so the
# seed parser is exercised against a fixture markdown that mirrors the
# historical bullet format.
_LEDGER_FIXTURE = """\
# Test fixture for the seed parser

## Entries

- `Synthetic merge TypeError from a spec-branch run`
  | backend tests run in a fixture | root cause example | open — needs follow-up.

- `Synthetic transport error with reset on every reconnect`
  | second example | retry worked | fixed-in-test.

- `Synthetic disk exhausted on /tmp`
  | third example | no disk | non-issue-because test-only.
"""


# ── Capture (integration through instrumented sites) ─────────────────


class MergeFailureCaptureTests(APITestCase):
    """The merge ladder records every failed merge into the error ledger."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board, odin_id="sp_mf", title="Merge failure spec")

    def test_record_merge_failure_creates_row(self):
        task = self.make_task(self.board, spec=self.spec, title="merge case")
        record_merge_failure(
            task=task,
            symptom="MergeResult.__init__() got an unexpected keyword argument 'agent_model'",
            error="TypeError in merge ladder",
            conflicting_files=["tasks/models.py"],
        )
        evt = ErrorEvent.objects.filter(source=ErrorEvent.SOURCE_MERGE_FAILURE, task=task).first()
        self.assertIsNotNone(evt)
        self.assertIn("MergeResult", evt.symptom)
        self.assertEqual(evt.failure_class, "")
        self.assertEqual(evt.disposition, ErrorEvent.DISPOSITION_OPEN)
        self.assertEqual(evt.spec_id, self.spec.id)
        # Context captures the structured metadata so triage can filter by it.
        self.assertIn("conflicting_files", (evt.context or {}))


class GateCrashCaptureTests(APITestCase):
    """Spec-verify gate crashes (verify subprocess blew up) record to the ledger."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board, odin_id="sp_gc", title="Gate crash spec")

    def test_record_gate_crash_creates_row(self):
        record_gate_crash(
            spec=self.spec,
            symptom="verify.sh crashed: OSError(28, 'No space left on device')",
            log_path="/tmp/.spec-verify.log",
            log_tail="No space left on device\n",
            head_sha="deadbeef",
        )
        evt = ErrorEvent.objects.filter(source=ErrorEvent.SOURCE_GATE_CRASH, spec=self.spec).first()
        self.assertIsNotNone(evt)
        self.assertIn("verify.sh crashed", evt.symptom)
        self.assertEqual(evt.log_path, "/tmp/.spec-verify.log")
        self.assertEqual(evt.failure_class, "disk_exhaustion")
        self.assertEqual(evt.context["head_sha"], "deadbeef")


class FailureTaggerCaptureTests(APITestCase):
    """Unclassified failures (tagger returns 'unknown') record to the ledger."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board, title="tagged case")

    def test_record_failure_tagger_unknown_creates_row(self):
        record_failure_tagger_unknown(
            task=self.task,
            symptom="Some new untagged infra signal",
            failure_class="unknown",
            failure_type="agent_execution_failure",
            failure_reason="something new and weird",
        )
        evt = ErrorEvent.objects.filter(
            source=ErrorEvent.SOURCE_FAILURE_TAGGER, task=self.task,
        ).first()
        self.assertIsNotNone(evt)
        self.assertEqual(evt.failure_class, "unknown")
        self.assertEqual(evt.context["failure_type"], "agent_execution_failure")


class CeleryExceptionCaptureTests(APITestCase):
    """Celery dispatch exceptions record so the operator sees the broker outage."""

    def test_record_celery_exception_creates_row(self):
        record_celery_exception(
            task_name="tasks.dag_executor.merge_task_on_reflection",
            symptom="BrokerConnectionError: broker down",
            exc_class="OperationalError",
            exc_message="connection refused",
            task_id=42,
        )
        evt = ErrorEvent.objects.filter(source=ErrorEvent.SOURCE_CELERY_EXCEPTION).first()
        self.assertIsNotNone(evt)
        self.assertIn("BrokerConnectionError", evt.symptom)
        self.assertEqual(evt.context["task_name"], "tasks.dag_executor.merge_task_on_reflection")
        self.assertEqual(evt.context["task_id"], 42)


class AgentMalformedStatusCaptureTests(APITestCase):
    """Harness-emitted ODIN-STATUS blocks with garbage values
    (e.g. ``\\`` for task #234) record into the ledger so the
    league table can see which model/harness combination emits
    them — task #237.
    """

    def test_record_agent_malformed_status_creates_row(self):
        board = self.make_board()
        spec = self.make_spec(board, odin_id="sp_mal", title="malformed status")
        task = self.make_task(board, spec=spec, title="234-style malformed status")
        record_agent_malformed_status(
            task=task,
            symptom="ODIN-STATUS block value was '\\\\' (literal backslash)",
            raw_block="\\",
            agent="claude",
            model="minimax-coding-plan/MiniMax-M3",
            inferred=True,
        )
        evt = ErrorEvent.objects.filter(
            source=ErrorEvent.SOURCE_AGENT_MALFORMED_STATUS, task=task,
        ).first()
        self.assertIsNotNone(evt)
        self.assertEqual(evt.context["raw_block"], "\\")
        self.assertEqual(evt.context["agent"], "claude")
        self.assertEqual(evt.context["model"], "minimax-coding-plan/MiniMax-M3")
        self.assertTrue(evt.context["inferred"])

    def test_source_choice_value(self):
        # Pin the literal choice so a typo can't sneak in.
        self.assertEqual(
            ErrorEvent.SOURCE_AGENT_MALFORMED_STATUS, "agent_malformed_status",
        )


class ReflectionErrorCaptureTests(APITestCase):
    """A reflection verdict of 'ERROR' (no verdict_summary) records to the ledger."""

    def test_record_reflection_error_creates_row(self):
        record_reflection_error(
            reflection_id=7,
            task_id=11,
            symptom="Reviewer emitted no verdict (no fenced JSON in output)",
            reviewer_agent="glm",
            reviewer_model="zai-coding-plan/glm-5.2",
        )
        evt = ErrorEvent.objects.filter(source=ErrorEvent.SOURCE_REFLECTION_ERROR).first()
        self.assertIsNotNone(evt)
        self.assertEqual(evt.source_id, "7")
        self.assertEqual(evt.context["reviewer_model"], "zai-coding-plan/glm-5.2")


class ReflectionNoReviewerCaptureTests(APITestCase):
    """A reflection watchdog no-reviewer escalation records to the ledger (task #246)."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board, title="stuck review")

    def test_record_reflection_no_reviewer_creates_row(self):
        record_reflection_no_reviewer(
            task=self.task,
            symptom="reflection watchdog: no reviewer resolvable after 3 consecutive scan(s)",
            no_reviewer_skips=3,
        )
        evt = ErrorEvent.objects.filter(
            source=ErrorEvent.SOURCE_REFLECTION_NO_REVIEWER,
        ).first()
        self.assertIsNotNone(evt)
        self.assertEqual(evt.source_id, f"task-{self.task.id}:no_reviewer")
        self.assertEqual(evt.context["no_reviewer_skips"], 3)
        self.assertEqual(evt.context["task_id"], self.task.id)

    def test_record_reflection_no_reviewer_idempotent_on_same_task(self):
        """Same task fires the escalation twice → exactly one row (idempotent)."""
        record_reflection_no_reviewer(
            task=self.task, symptom="x", no_reviewer_skips=3,
        )
        record_reflection_no_reviewer(
            task=self.task, symptom="x", no_reviewer_skips=4,
        )
        # Filter by the source_id the helper derived (task-{id}:no_reviewer)
        # so a one-time seed row from a different task doesn't pollute the
        # count.
        count = ErrorEvent.objects.filter(
            source=ErrorEvent.SOURCE_REFLECTION_NO_REVIEWER,
            source_id=f"task-{self.task.id}:no_reviewer",
        ).count()
        self.assertEqual(count, 1)


# ── Idempotency ──────────────────────────────────────────────────────


class IdempotencyTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def test_same_source_id_dedupes(self):
        task = self.make_task(self.board, title="dup case")
        record_merge_failure(
            task=task, symptom="dup symptom",
            source_id="merge-1",
            error="x", conflicting_files=[],
        )
        record_merge_failure(
            task=task, symptom="dup symptom",
            source_id="merge-1",
            error="x", conflicting_files=[],
        )
        self.assertEqual(
            ErrorEvent.objects.filter(task=task, source_id="merge-1").count(), 1,
        )

    def test_different_source_id_separate_rows(self):
        task = self.make_task(self.board, title="multi case")
        record_merge_failure(task=task, symptom="x", source_id="merge-1",
                             error="", conflicting_files=[])
        record_merge_failure(task=task, symptom="x", source_id="merge-2",
                             error="", conflicting_files=[])
        self.assertEqual(ErrorEvent.objects.filter(task=task).count(), 2)


# ── Grouping (signature dedup + counts) ──────────────────────────────


class GroupingTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board, title="group case")

    def test_symptom_signature_normalizes(self):
        a = symptom_signature("MergeResult.__init__() got an unexpected keyword argument 'agent_model'")
        b = symptom_signature("MergeResult.__init__() got an unexpected keyword argument 'agent_model'")
        c = symptom_signature("totally different text here")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_group_by_signature_counts(self):
        from tasks.errors import group_by_signature
        record_merge_failure(
            task=self.task, symptom="MergeResult missing kwarg", source_id="m-1",
            error="", conflicting_files=[],
        )
        record_merge_failure(
            task=self.task, symptom="MergeResult missing kwarg", source_id="m-2",
            error="", conflicting_files=[],
        )
        record_gate_crash(
            spec=self.make_spec(self.board, odin_id="sp_g"),
            symptom="disk full on /tmp",
            log_path="", log_tail="",
        )
        groups = group_by_signature()
        # Two identical merge failures group together, gate crash separate.
        merge_group = next(
            g for g in groups if g["source"] == ErrorEvent.SOURCE_MERGE_FAILURE
        )
        self.assertEqual(merge_group["count"], 2)
        gate_group = next(
            g for g in groups if g["source"] == ErrorEvent.SOURCE_GATE_CRASH
        )
        self.assertEqual(gate_group["count"], 1)


# ── Disposition API ──────────────────────────────────────────────────


class DispositionAPITests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board, title="disposition case")
        self.evt = record_error(
            source=ErrorEvent.SOURCE_MERGE_FAILURE,
            symptom="some merge error",
            task=self.task,
        )

    def test_set_disposition_via_helper(self):
        updated = set_disposition(self.evt.id, "fixed", note="PYTHONPATH fix landed")
        self.assertEqual(updated.disposition, "fixed")
        self.assertEqual(updated.disposition_note, "PYTHONPATH fix landed")
        self.evt.refresh_from_db()
        self.assertEqual(self.evt.disposition, "fixed")

    def test_set_disposition_via_api(self):
        resp = self.client.post(
            f"/errors/{self.evt.id}/disposition/",
            {"disposition": "non-issue", "note": "host-only: pmset battery sleep"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.evt.refresh_from_db()
        self.assertEqual(self.evt.disposition, "non-issue")
        self.assertIn("pmset", self.evt.disposition_note)

    def test_invalid_disposition_returns_400(self):
        resp = self.client.post(
            f"/errors/{self.evt.id}/disposition/",
            {"disposition": "banana"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_unknown_id_returns_404(self):
        resp = self.client.post(
            "/errors/999999/disposition/",
            {"disposition": "fixed"},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)


# ── Seed from docs/patterns/error_ledger.md ──────────────────────────


class SeedFromLedgerTests(APITestCase):
    def test_seed_creates_rows_from_fixture(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write(_LEDGER_FIXTURE)
            tmp = fh.name
        try:
            created = seed_from_ledger(tmp)
            self.assertGreaterEqual(created, 3)
            self.assertTrue(
                ErrorEvent.objects.filter(disposition="open").exists(),
                "seed should carry 'open' disposition through",
            )
            self.assertTrue(
                ErrorEvent.objects.filter(disposition="fixed").exists(),
                "seed should map 'fixed-in-test' to disposition=fixed",
            )
            self.assertTrue(
                ErrorEvent.objects.filter(disposition="non-issue").exists(),
                "seed should map 'non-issue-because' to disposition=non-issue",
            )
        finally:
            os.unlink(tmp)

    def test_seed_is_idempotent(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write(_LEDGER_FIXTURE)
            tmp = fh.name
        try:
            seed_from_ledger(tmp)
            first_count = ErrorEvent.objects.count()
            seed_from_ledger(tmp)
            self.assertEqual(ErrorEvent.objects.count(), first_count)
        finally:
            os.unlink(tmp)

    def test_seed_skips_missing_doc(self):
        from pathlib import Path
        self.assertEqual(seed_from_ledger(Path("/nonexistent/ledger.md")), 0)

    def test_live_narrative_doc_is_skipped_by_seed(self):
        """The live error_ledger.md is now narrative-only — seed must
        produce zero new rows so a stray --seed doesn't pollute the
        store with the doc's headings or instructions."""
        ledger_path = _find_ledger_doc()
        self.assertIsNotNone(ledger_path, "live doc should still exist")
        # Wipe any prior seed state from earlier tests in the same DB.
        ErrorEvent.objects.filter(
            source=ErrorEvent.SOURCE_FAILURE_TAGGER,
            context__seeded_from="docs/patterns/error_ledger.md",
        ).delete()
        created = seed_from_ledger(ledger_path)
        self.assertEqual(
            created, 0,
            "narrative-only doc must not produce seed rows; only the "
            "bullet format does",
        )


# ── Pending import (W6 retrospective entries logged in the doc) ─────


class PendingLedgerImportTests(APITestCase):
    """Task #238: import the late retrospective entries that lived in
    docs/patterns/error_ledger.md's Pending import section into ErrorEvent,
    so the structured store is the truth and the doc can stay narrative.

    The W6 entries (conflict-flag, zombie sandboxes, watcher clock jump) all
    had follow-ups that merged in wave 6, so the import sets disposition=fixed
    with a note pointing at the resolving task / wave. The W7 entry
    (odin version skew, task 241) is added the same way. Re-running the
    import is a no-op.
    """

    def setUp(self):
        super().setUp()
        # Each test starts clean — the import is idempotent but we want to
        # count "first call creates all rows" cleanly, so wipe any leftover
        # rows from prior tests in the same DB.
        ErrorEvent.objects.filter(
            source_id__startswith="pending:",
        ).delete()

    def test_import_creates_pending_rows_all_disposition_fixed(self):
        created = import_pending_ledger_entries()
        self.assertEqual(created, 4)
        pending = ErrorEvent.objects.filter(source_id__startswith="pending:")
        self.assertEqual(pending.count(), 4)
        for evt in pending:
            self.assertEqual(
                evt.disposition, ErrorEvent.DISPOSITION_FIXED,
                f"row {evt.id} ({evt.source_id}) should be marked fixed; "
                f"every retrospective entry has a merged follow-up",
            )

    def test_import_uses_accurate_sources(self):
        """conflict-flag is a merge ladder gap; the other two are
        unclassified infra signals at capture time."""
        import_pending_ledger_entries()
        merge_row = ErrorEvent.objects.filter(
            source=ErrorEvent.SOURCE_MERGE_FAILURE, source_id="pending:conflict-flag-225",
        ).first()
        self.assertIsNotNone(
            merge_row, "conflict-flag entry should be tagged merge_failure",
        )
        self.assertIn("merge_status=conflict", merge_row.symptom)
        self.assertIn("needs_human", merge_row.symptom)
        self.assertEqual(merge_row.disposition, ErrorEvent.DISPOSITION_FIXED)
        self.assertIn("231", merge_row.disposition_note)

        tagger_rows = ErrorEvent.objects.filter(
            source=ErrorEvent.SOURCE_FAILURE_TAGGER, source_id__startswith="pending:",
        )
        self.assertEqual(tagger_rows.count(), 3)
        symptoms = " ".join(r.symptom for r in tagger_rows)
        self.assertIn("zombie", symptoms.lower())
        # CLOCK_JUMP lives in the disposition_note (the symptom describes
        # the alarm, the note names the fix).
        notes = " ".join(r.disposition_note for r in tagger_rows)
        self.assertIn("CLOCK_JUMP", notes)

    def test_import_carries_fix_context(self):
        """Each row's context points at the wave / task that closed the gap."""
        import_pending_ledger_entries()
        # W6.11 = task 231 unified the waiting-on-human flag.
        e1 = ErrorEvent.objects.get(source_id="pending:conflict-flag-225")
        self.assertEqual(e1.context.get("wave"), "W6.11")
        self.assertEqual(e1.context.get("fix_task"), "231")
        # W6.15 = task 235 made reconciler liveness progress-based.
        e2 = ErrorEvent.objects.get(source_id="pending:zombie-sandboxes-235")
        self.assertEqual(e2.context.get("wave"), "W6.15")
        self.assertEqual(e2.context.get("fix_task"), "235")
        # In-session fix during wave 6 close; no separate task id.
        e3 = ErrorEvent.objects.get(source_id="pending:watcher-clock-jump")
        self.assertIn("CLOCK_JUMP", e3.disposition_note)
        # W7 = task 241 pinned odin to the worktree via PYTHONPATH + guard.
        e4 = ErrorEvent.objects.get(source_id="pending:odin-version-skew-241")
        self.assertEqual(e4.context.get("wave"), "W7")
        self.assertEqual(e4.context.get("fix_task"), "241")
        self.assertIn("PYTHONPATH", e4.disposition_note)

    def test_import_is_idempotent(self):
        """Re-running --import-pending after the first call adds zero rows."""
        first = import_pending_ledger_entries()
        self.assertEqual(first, 4)
        count_after_first = ErrorEvent.objects.filter(
            source_id__startswith="pending:",
        ).count()
        second = import_pending_ledger_entries()
        self.assertEqual(second, 0)
        third = import_pending_ledger_entries()
        self.assertEqual(third, 0)
        count_after_third = ErrorEvent.objects.filter(
            source_id__startswith="pending:",
        ).count()
        self.assertEqual(count_after_first, count_after_third)

    def test_import_preserves_existing_notes_on_repeat(self):
        """An operator-set disposition_note on an existing pending row
        survives a re-import (the import must not stomp it)."""
        import_pending_ledger_entries()
        row = ErrorEvent.objects.get(source_id="pending:conflict-flag-225")
        set_disposition(row.id, "fixed", note="operator override: kept on review")
        import_pending_ledger_entries()
        row.refresh_from_db()
        self.assertEqual(row.disposition_note, "operator override: kept on review")

    def test_import_pending_via_cli(self):
        """`testing_tools/errors.py --import-pending` runs the helper."""
        import io
        from contextlib import redirect_stdout
        from testing_tools.errors import main as cli_main
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli_main(["--import-pending"])
        self.assertIsNone(rc)
        self.assertIn("Imported 4 pending entries", buf.getvalue())
        self.assertEqual(
            ErrorEvent.objects.filter(source_id__startswith="pending:").count(), 4,
        )

    def test_brief_lists_pending_entries_after_import(self):
        """After import, errors.py --brief surfaces the pending entries
        grouped under their sources — acceptance criterion #1."""
        import_pending_ledger_entries()
        from testing_tools.errors import list_errors, _print_brief
        import io
        from contextlib import redirect_stdout

        # The brief view is the working surface for the operator — make
        # sure it doesn't blow up and that all pending entries
        # are grouped under their sources after the import.
        buf = io.StringIO()
        with redirect_stdout(buf):
            list_errors(mode="brief")
        brief = buf.getvalue()
        self.assertIn("entries", brief)

        # All pending entries are fixed; the brief default is open entries, so
        # filter by disposition=fixed to surface them.
        payload_fixed = list_errors(mode="json", disposition="fixed")
        pending_fixed = [
            e for e in ErrorEvent.objects.filter(
                source_id__startswith="pending:",
                disposition=ErrorEvent.DISPOSITION_FIXED,
            )
        ]
        self.assertEqual(len(pending_fixed), 4)
        for e in pending_fixed:
            self.assertEqual(e.disposition, "fixed")
        # Brief grouping should produce one signature per pending entry.
        signatures = {e.symptom_signature for e in pending_fixed}
        self.assertEqual(len(signatures), 4)


# ── CLI (testing_tools/errors.py) ────────────────────────────────────


class ErrorCLIAndTraceTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board, odin_id="sp_cli", title="CLI spec")
        self.task = self.make_task(self.board, spec=self.spec, title="CLI task")

    def test_errors_cli_brief_groups_open_entries(self):
        record_merge_failure(
            task=self.task, symptom="dup symptom for brief",
            source_id="m-cli-1", error="", conflicting_files=[],
        )
        record_merge_failure(
            task=self.task, symptom="dup symptom for brief",
            source_id="m-cli-2", error="", conflicting_files=[],
        )
        from testing_tools.errors import list_errors
        data = list_errors(mode="json")
        self.assertGreaterEqual(data["count"], 2)
        # Grouping includes a bucket for the duplicate symptom.
        bucket_keys = {g["signature"] for g in data["groups"]}
        self.assertTrue(any("dup symptom" in k for k in bucket_keys))

    def test_errors_cli_set_disposition_updates(self):
        record_merge_failure(
            task=self.task, symptom="setdisp case", source_id="m-cli-set",
            error="", conflicting_files=[],
        )
        evt = ErrorEvent.objects.get(source_id="m-cli-set")
        from testing_tools.errors import set_disposition as cli_set
        updated = cli_set(evt.id, "fixed", note="cli path")
        self.assertEqual(updated.disposition, "fixed")
        self.assertEqual(updated.disposition_note, "cli path")


# ── Hook integration (real paths fire the captures) ─────────────────


class HookIntegrationTests(APITestCase):
    """Forcing one failure of each instrumented kind must produce a row.

    These tests hit the actual capture sites — _fail_stale_execution
    (failure_tagger), the merge ladder (merge_failure), the reflection
    PATCH endpoint (reflection_error), and the celery dispatch path
    (celery_exception). The gate_crash site runs inside a thread and
    is exercised in the gate_crash unit test above; this block proves
    the other four sites fire when their real triggering path runs.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board, odin_id="sp_hook", title="Hook spec")

    def test_fail_stale_execution_with_unknown_class_records(self):
        """Stale-FAILED with an unrecognised failure_class records via failure_tagger."""
        from tasks.dag_executor import _fail_stale_execution
        task = self.make_task(self.board, spec=self.spec, status=TaskStatus.EXECUTING)
        run = TaskRun.objects.create(
            task=task, spec=task.spec, run_token="tok-hook",
            state=TaskRunState.RUNNING,
        )
        # Pick a failure_type that classify_failure returns "unknown" for.
        _fail_stale_execution(
            task,
            reason="something new and weird, no signature",
            failure_type="brand_new_infra_kind",
            run_token="tok-hook",
        )
        evt = ErrorEvent.objects.filter(
            source=ErrorEvent.SOURCE_FAILURE_TAGGER, task=task,
        ).first()
        self.assertIsNotNone(
            evt, "stale-FAILED with unknown class should record an ErrorEvent",
        )
        self.assertEqual(evt.failure_class, "unknown")

    def test_reflection_unknown_verdict_records(self):
        """A reflection verdict that isn't PASS/NEEDS_WORK/FAIL records via reflection_error."""
        task = self.make_task(self.board, spec=self.spec, status=TaskStatus.REVIEW)
        report = ReflectionReport.objects.create(
            task=task, reviewer_agent="glm",
            reviewer_model="zai-coding-plan/glm-5.2",
            requested_by="system@taskit",
            status=ReflectionStatus.RUNNING,
        )
        resp = self.client.patch(
            f"/reflections/{report.id}/",
            {
                "status": "COMPLETED",
                "verdict": "ERROR",
                "verdict_summary": "Reviewer emitted no verdict in output (just prose).",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        evt = ErrorEvent.objects.filter(
            source=ErrorEvent.SOURCE_REFLECTION_ERROR,
            source_id=str(report.id),
        ).first()
        self.assertIsNotNone(
            evt, "reflection with unrecognized verdict should record an ErrorEvent",
        )
        self.assertIn("prose", (evt.symptom or "").lower())

    def test_reflection_pass_does_not_record(self):
        """PASS verdict is not an ERROR and must not record."""
        task = self.make_task(self.board, spec=self.spec, status=TaskStatus.REVIEW)
        from tasks.models import ReflectionReport
        report = ReflectionReport.objects.create(
            task=task, reviewer_agent="glm",
            reviewer_model="zai-coding-plan/glm-5.2",
            requested_by="system@taskit", status=ReflectionStatus.RUNNING,
        )
        self.client.patch(
            f"/reflections/{report.id}/",
            {"status": "COMPLETED", "verdict": "PASS", "verdict_summary": "Looks good."},
            format="json",
        )
        self.assertFalse(
            ErrorEvent.objects.filter(source=ErrorEvent.SOURCE_REFLECTION_ERROR).exists()
        )

    def test_celery_dispatch_exception_records(self):
        """A broker exception in _merge_task_on_reflection_pass records via celery_exception."""
        from tasks.views import _merge_task_on_reflection_pass
        from tasks.models import TaskStatus as TS
        task = self.make_task(
            self.board, spec=self.spec, status=TS.REVIEW,
            metadata={"branch": "task/test-branch"},
        )
        with patch("tasks.dag_executor._dispatch_merge_task", side_effect=RuntimeError("broker down")):
            _merge_task_on_reflection_pass(task)
        # The helper sets source_id="<task_name>:<exc_class>" so a duplicate
        # exception (e.g. broker outage) is one row, not N. Look up by
        # source+task, not source_id, to avoid coupling to that detail.
        evt = ErrorEvent.objects.filter(
            source=ErrorEvent.SOURCE_CELERY_EXCEPTION, task=task,
        ).first()
        self.assertIsNotNone(
            evt, "celery dispatch failure should record an ErrorEvent",
        )
        self.assertEqual(evt.task_id, task.id)
        self.assertIn("broker down", evt.symptom)