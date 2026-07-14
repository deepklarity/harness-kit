"""Error ledger (task #222).

Every error the system already handles/logs — failure_tagger misses, merge
ladder failures, reflection ERROR verdicts, spec-verify gate crashes,
celery task exceptions — is captured here as an :class:`ErrorEvent` row
with the structured context an operator needs to triage later.

Why a separate model from :class:`tasks.models.MistakeEntry` (W6.1)?

- MistakeEntry is the per-task contract for twins warnings: one row per
  reflection report or execution run, ``task`` is required, and it carries
  no disposition lifecycle. Loosening the not-null ``task`` FK to fit
  celery worker crashes (which happen before any task exists) or adding a
  disposition field the twins path never reads would couple two
  unrelated contracts in one table.
- ErrorEvent is the system-wide triage surface: nullable task/spec,
  a disposition lifecycle (``open``/``fixed``/``non-issue``) settable
  via API, and a normalized ``symptom_signature`` for grouping visually
  identical errors regardless of source.

The two complement each other — MistakeEntry drives the prevention
signal on similar future tasks, ErrorEvent drives the operator's
triage queue for current and historical errors.

Dedup key: ``(source, source_id)`` when ``source_id`` is non-empty;
otherwise allow multiple rows with the same symptom (e.g., repeated
celery broker outages without an event id).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from django.db.models import Q

from .failure_tagger import classify_failure_text
from .models import ErrorEvent, Spec, Task


SYMPTOM_LIMIT = 500
SIGNATURE_LIMIT = 200
SYMPTOM_PREVIEW = 80

_DISPOSITION_VALUES = {choice for choice, _ in ErrorEvent.DISPOSITION_CHOICES}
SOURCE_VALUES = {choice for choice, _ in ErrorEvent.SOURCE_CHOICES}

# Multi-whitespace collapse — symptoms must group even when the same
# error has slightly different spacing across captures.
_WHITESPACE_RE = re.compile(r"\s+")
# Strip leading/trailing punctuation that varies across captures but
# carries no signal for grouping.
_PUNCT_TRIM_RE = re.compile(r"^[^A-Za-z0-9]+|[^A-Za-z0-9.]+$")


def _normalize(symptom: str) -> str:
    text = (symptom or "").strip().lower()
    text = _WHITESPACE_RE.sub(" ", text)
    text = _PUNCT_TRIM_RE.sub("", text)
    return text


def symptom_signature(symptom: str) -> str:
    """Stable, low-cardinality signature for grouping identical symptoms.

    The first ``SYMPTOM_PREVIEW`` normalized characters are enough to
    distinguish the live-case symptoms the ledger tracks today; the
    ``length`` suffix prevents collisions between a 60-char prefix and a
    full-sentence symptom that happens to share it. Capped to
    ``SIGNATURE_LIMIT`` to fit the column.
    """
    normalized = _normalize(symptom)
    preview = normalized[:SYMPTOM_PREVIEW]
    return f"{preview}|len={len(normalized)}"[:SIGNATURE_LIMIT]


def _clip(text: str, limit: int) -> str:
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def record_error(
    *,
    source: str,
    symptom: str,
    failure_class: str = "",
    source_id: str = "",
    task: Optional[Task] = None,
    spec: Optional[Spec] = None,
    log_path: str = "",
    log_tail: str = "",
    context: Optional[Dict] = None,
    disposition: str = ErrorEvent.DISPOSITION_OPEN,
) -> ErrorEvent:
    """Append one structured error event to the ledger.

    Dedup: ``(source, source_id)`` when ``source_id`` is non-empty
    (idempotent on the same event id).  When ``source_id`` is blank
    (e.g., ad-hoc operator capture) the row is always inserted.
    """
    if source not in SOURCE_VALUES:
        raise ValueError(f"Unknown source: {source!r}")
    if disposition not in _DISPOSITION_VALUES:
        raise ValueError(f"Unknown disposition: {disposition!r}")
    symptom = _clip((symptom or "").strip(), SYMPTOM_LIMIT)
    if not symptom:
        raise ValueError("symptom must be non-empty")

    # Skip dedup when source_id is blank — let the operator add
    # repeated captures freely.
    if source_id:
        existing = ErrorEvent.objects.filter(source=source, source_id=source_id).first()
        if existing is not None:
            return existing

    spec = spec if spec is not None else (task.spec if task else None)
    signature = symptom_signature(symptom)
    return ErrorEvent.objects.create(
        source=source,
        source_id=source_id or "",
        symptom=symptom,
        symptom_signature=signature,
        failure_class=failure_class or "",
        task=task,
        spec=spec,
        log_path=log_path or "",
        log_tail=log_tail or "",
        context=context or {},
        disposition=disposition,
    )


def set_disposition(
    event_id: int,
    disposition: str,
    note: str = "",
) -> ErrorEvent:
    """Update the disposition (and optional note) for one ledger entry.

    Returns the refreshed row. Raises ``ErrorEvent.DoesNotExist`` when
    ``event_id`` is unknown; raises ``ValueError`` for an unknown
    disposition value (caller maps to HTTP 400).
    """
    if disposition not in _DISPOSITION_VALUES:
        raise ValueError(f"Unknown disposition: {disposition!r}")
    evt = ErrorEvent.objects.get(id=event_id)
    evt.disposition = disposition
    if note:
        evt.disposition_note = note
    evt.save(update_fields=["disposition", "disposition_note", "updated_at"])
    return evt


def group_by_signature(
    *,
    disposition: Optional[str] = None,
    source: Optional[str] = None,
    board=None,
) -> List[Dict]:
    """Group error events by (source, symptom_signature) for triage.

    Returns a list of dicts, one per signature, newest first, with the
    signature, source, latest symptom, latest disposition, count, and
    the underlying event ids. Filterable by ``disposition`` (default:
    open entries — the operator's working set), ``source``, and
    ``board`` (matches errors linked via either ``task__board`` or
    ``spec__board``, since an ErrorEvent may be attributed through
    either FK).
    """
    qs = ErrorEvent.objects.all()
    if disposition:
        qs = qs.filter(disposition=disposition)
    if source:
        qs = qs.filter(source=source)
    if board is not None:
        qs = qs.filter(Q(task__board=board) | Q(spec__board=board))

    grouped: Dict[tuple, Dict] = {}
    for evt in qs.order_by("-created_at", "-id"):
        key = (evt.source, evt.symptom_signature)
        bucket = grouped.setdefault(key, {
            "source": evt.source,
            "signature": evt.symptom_signature,
            "latest_symptom": evt.symptom,
            "latest_disposition": evt.disposition,
            "latest_at": evt.created_at,
            "count": 0,
            "event_ids": [],
        })
        bucket["count"] += 1
        bucket["event_ids"].append(evt.id)

    # Newest signature first, then highest count for stability.
    return sorted(
        grouped.values(),
        key=lambda b: (b["latest_at"], b["count"]),
        reverse=True,
    )


# ── Capture helpers (one per instrumented kind) ─────────────────────
# Each helper preserves a small, consistent shape so the call sites
# don't need to repeat the field names.

def record_merge_failure(
    *,
    task: Optional[Task],
    symptom: str,
    error: str = "",
    conflicting_files: Optional[Iterable[str]] = None,
    source_id: str = "",
) -> ErrorEvent:
    """Capture a merge ladder failure (dag_executor.py post-merge path)."""
    context = {
        "error": error or "",
        "conflicting_files": list(conflicting_files or []),
    }
    return record_error(
        source=ErrorEvent.SOURCE_MERGE_FAILURE,
        symptom=symptom,
        source_id=source_id,
        task=task,
        context=context,
    )


def record_gate_crash(
    *,
    spec: Optional[Spec],
    symptom: str,
    log_path: str = "",
    log_tail: str = "",
    head_sha: str = "",
    source_id: str = "",
) -> ErrorEvent:
    """Capture a spec-verify gate crash (spec_verify.py exception path)."""
    context = {"head_sha": head_sha or ""}
    if not source_id and head_sha:
        # The spec + head SHA uniquely identifies a verify run; use it as
        # the dedup key so a retried run after a transient crash still
        # surfaces as one entry.
        source_id = f"{spec.odin_id if spec else 'spec'}:{head_sha}"
    return record_error(
        source=ErrorEvent.SOURCE_GATE_CRASH,
        symptom=symptom,
        failure_class=classify_failure_text(symptom) if symptom else "",
        source_id=source_id,
        spec=spec,
        log_path=log_path,
        log_tail=log_tail,
        context=context,
    )


def record_failure_tagger_unknown(
    *,
    task: Optional[Task],
    symptom: str,
    failure_class: str = "unknown",
    failure_type: str = "",
    failure_reason: str = "",
    source_id: str = "",
) -> ErrorEvent:
    """Capture a failure_tagger classification miss (any 'unknown' class)."""
    if not source_id and task is not None:
        # Same task + same failure_type is the same event — re-classifying
        # a task N times during a retry loop should not pile up rows.
        source_id = f"task-{task.id}:{failure_type}"
    context = {
        "failure_type": failure_type or "",
        "failure_reason": failure_reason or "",
    }
    return record_error(
        source=ErrorEvent.SOURCE_FAILURE_TAGGER,
        symptom=symptom,
        failure_class=failure_class,
        source_id=source_id,
        task=task,
        context=context,
    )


def record_reflection_error(
    *,
    reflection_id: int,
    task_id: Optional[int],
    symptom: str,
    reviewer_agent: str = "",
    reviewer_model: str = "",
    source_id: str = "",
) -> ErrorEvent:
    """Capture a reflection ERROR verdict (no fenced JSON in reviewer output)."""
    if not source_id:
        source_id = str(reflection_id)
    context = {
        "reflection_id": reflection_id,
        "task_id": task_id,
        "reviewer_agent": reviewer_agent or "",
        "reviewer_model": reviewer_model or "",
    }
    return record_error(
        source=ErrorEvent.SOURCE_REFLECTION_ERROR,
        symptom=symptom,
        source_id=source_id,
        context=context,
    )


def record_reflection_no_reviewer(
    *,
    task: Optional[Task],
    symptom: str,
    no_reviewer_skips: int,
    source_id: str = "",
) -> ErrorEvent:
    """Capture a reflection watchdog no-reviewer escalation (task #246).

    Fires when ``select_reviewer_by_context_size`` returns ``(None, None)``
    for N consecutive watchdog scans. Same task + same skip-count = one row
    (idempotent), so a long-running stuck review doesn't pile up entries;
    a fresh escalation (counter reset by a successful dispatch) gets a new
    row.
    """
    if not source_id and task is not None:
        source_id = f"task-{task.id}:no_reviewer"
    context = {
        "task_id": task.id if task is not None else None,
        "no_reviewer_skips": no_reviewer_skips,
    }
    return record_error(
        source=ErrorEvent.SOURCE_REFLECTION_NO_REVIEWER,
        symptom=symptom,
        source_id=source_id,
        task=task,
        context=context,
    )


def record_celery_exception(
    *,
    task_name: str,
    symptom: str,
    exc_class: str = "",
    exc_message: str = "",
    task: Optional[Task] = None,
    task_id: Optional[int] = None,
    source_id: str = "",
) -> ErrorEvent:
    """Capture a celery task exception (broker outage, dispatch failure).

    ``task`` is the optional Task row to FK to (preferred when the
    capture site has it). ``task_id`` is kept for the call sites that
    only know the id; it is captured into ``context`` so the operator
    can still trace it back even when no Task row exists.
    """
    context = {
        "task_name": task_name or "",
        "exc_class": exc_class or "",
        "exc_message": exc_message or "",
        "task_id": task_id if task_id is not None else (task.id if task else None),
    }
    if not source_id and task_name and exc_class:
        source_id = f"{task_name}:{exc_class}"
    return record_error(
        source=ErrorEvent.SOURCE_CELERY_EXCEPTION,
        symptom=symptom,
        source_id=source_id,
        task=task,
        context=context,
    )


def record_agent_malformed_status(
    *,
    task: Optional[Task],
    symptom: str,
    raw_block: str = "",
    agent: str = "",
    model: str = "",
    inferred: bool = False,
    source_id: str = "",
) -> ErrorEvent:
    """Capture a harness-emitted ODIN-STATUS block whose value was not
    ``SUCCESS`` or ``FAILED`` (task #237).

    Observed live on task #234: the agent emitted a literal backslash
    for the status word, the parser failed closed, and the run FAILED
    even though the agent had committed real work. The harness now
    forwards the raw block via ``TaskResult.metadata['malformed_status']``
    so the orchestrator can record it here, and the league-table query
    can show which model/harness combination emits malformed blocks.

    ``raw_block`` is captured verbatim (the bad string the model
    produced) and ``inferred`` records whether the parser rescued the
    run via worktree inspection — both signals are required for
    triage: a raw-block capture without inference means the run
    actually failed; with inference, the run succeeded but the agent
    should be told to fix its output format.
    """
    if not source_id:
        # Dedup per task + agent + raw block — same model emitting the
        # same garbage twice is one entry, not N.
        source_id = f"task-{task.id if task else '?'}:{agent}:{raw_block[:40]}"
    context = {
        "raw_block": raw_block or "",
        "agent": agent or "",
        "model": model or "",
        "inferred": bool(inferred),
    }
    return record_error(
        source=ErrorEvent.SOURCE_AGENT_MALFORMED_STATUS,
        symptom=symptom,
        source_id=source_id,
        task=task,
        context=context,
    )


def record_comment_attribution_loss(
    *,
    task: Optional[Task],
    comment_id: Optional[int],
    author_email: str = "",
    comment_type: str = "",
    source_id: str = "",
) -> ErrorEvent:
    """Capture a comment created with the unknown@user fallback (task #250).

    ``unknown@user`` means the system lost track of who authored a comment —
    the verdict's reviewer, the agent, or the operator. That loss was silent
    (the value was just stored), so the reply-resume signal, the L2 counter,
    and humans all saw a lie where they expected an identity. This records it
    so the operator can triage attribution regressions instead of discovering
    them from a confused downstream flow.

    Fires from the TaskComment post_save signal whenever a new comment lands
    with ``author_email == "unknown@user"``, covering every creation path.
    Dedup is per comment (``source_id = comment id``) so each offending
    comment is exactly one row.
    """
    if not source_id and comment_id is not None:
        source_id = f"comment-{comment_id}"
    context = {
        "comment_id": comment_id,
        "author_email": author_email or "",
        "comment_type": comment_type or "",
    }
    return record_error(
        source=ErrorEvent.SOURCE_COMMENT_ATTRIBUTION_LOSS,
        symptom=(
            f"Comment {comment_id} on task {task.id if task else '?'} was "
            f"attributed to unknown@user — author identity was lost."
        ),
        source_id=source_id,
        task=task,
        context=context,
    )


def record_error_loop(
    *,
    task: Optional[Task],
    symptom: str,
    failure_class: str = "",
    dominant_signature: str = "",
    error_ratio: float = 0.0,
    error_lines: int = 0,
    total_lines: int = 0,
    trace_path: str = "",
    sample: str = "",
    source_id: str = "",
) -> ErrorEvent:
    """Capture an error-loop detection by the run reconciler (task #262).

    A run whose trace tail is dominated by repeated errors of the same
    signature is looping — the CLI is retrying a failing provider call
    forever, burning quota/tokens without progress. Each detection gets
    one ledger row so the operator sees the loop in the same triage
    surface as every other error, with the verdict (signature, ratio,
    sample) captured for triage.

    Dedup key: ``task + run_token`` (the source_id) so a re-reconcile
    pass on the same looping run never double-records.
    """
    if not source_id:
        source_id = f"task-{task.id if task else '?'}:{dominant_signature or failure_class}"
    context = {
        "dominant_signature": dominant_signature or "",
        "failure_class": failure_class or "",
        "error_ratio": round(float(error_ratio), 3),
        "error_lines": int(error_lines),
        "total_lines": int(total_lines),
        "sample": (sample or "")[:500],
    }
    return record_error(
        source=ErrorEvent.SOURCE_ERROR_LOOP,
        symptom=symptom,
        failure_class=failure_class or "",
        source_id=source_id,
        task=task,
        log_path=trace_path or "",
        context=context,
    )


# ── Seed (import the historical docs/patterns/error_ledger.md entries) ─


_ENTRY_HEAD_RE = re.compile(r"^-\s+`(?P<symptom>.+?)`\s*(?:\((?P<paren>[^)]*)\))?\s*$")
_CONTINUATION_RE = re.compile(r"^\s+\|\s*(?P<rest>.*)$")


def _parse_disposition(rest: str) -> str:
    """Pull the disposition out of the trailing text on a ledger bullet."""
    lower = rest.lower()
    if "non-issue" in lower or "non-issue-because" in lower:
        return ErrorEvent.DISPOSITION_NON_ISSUE
    if "fixed" in lower and "non-issue" not in lower:
        # "fixed in <task>" or "fixed-in-<task>" or "fixed in the wave-5 ..."
        return ErrorEvent.DISPOSITION_FIXED
    return ErrorEvent.DISPOSITION_OPEN


def seed_from_ledger(path: Path) -> int:
    """Import the historical entries from ``docs/patterns/error_ledger.md``.

    Idempotent on ``(source, source_id, symptom_signature)``: a re-seed
    of an unchanged doc returns 0 new rows.  The doc is rewritten over
    time, so each entry uses a stable source_id derived from its
    symptom hash to survive edits to surrounding context text.

    Returns the count of newly-created rows (0 when the seed is a no-op).
    """
    path = Path(path)
    if not path.exists():
        return 0
    lines = path.read_text().splitlines()

    created = 0
    i = 0
    while i < len(lines):
        head_match = _ENTRY_HEAD_RE.match(lines[i])
        if not head_match:
            i += 1
            continue
        symptom = head_match.group("symptom").strip()
        paren = (head_match.group("paren") or "").strip()
        # Pull the continuation block until the next `- \`` head or blank
        # boundary.
        i += 1
        continuation_lines = []
        while i < len(lines):
            line = lines[i]
            if _ENTRY_HEAD_RE.match(line):
                break
            cont = _CONTINUATION_RE.match(line)
            if cont:
                continuation_lines.append(cont.group("rest"))
                i += 1
            elif line.strip() == "":
                i += 1
                break
            else:
                i += 1
        if not symptom:
            continue

        # Stable per-entry id so the seed is idempotent across doc edits.
        stable_id = f"seed:{abs(hash(symptom)) & 0xffffffff:08x}"
        if ErrorEvent.objects.filter(
            source=ErrorEvent.SOURCE_FAILURE_TAGGER,
            source_id=stable_id,
        ).exists():
            continue

        rest_text = " ".join(continuation_lines)
        disposition = _parse_disposition(rest_text)
        context = {
            "seeded_from": "docs/patterns/error_ledger.md",
        }
        if paren:
            context["doc_label"] = paren
        if rest_text:
            context["doc_continuation"] = rest_text[:1000]
        # The seed lives under the SOURCE_FAILURE_TAGGER namespace —
        # these are all "we saw something fail" entries, before the
        # per-source taxonomy existed.  A future migration could split
        # them by topic; for now, one bucket keeps the seed simple.
        ErrorEvent.objects.create(
            source=ErrorEvent.SOURCE_FAILURE_TAGGER,
            source_id=stable_id,
            symptom=symptom[:SYMPTOM_LIMIT],
            symptom_signature=symptom_signature(symptom),
            context=context,
            disposition=disposition,
            disposition_note="seeded from manual ledger doc",
        )
        created += 1
    return created


# ── Pending import (W6 retrospective entries) ──────────────────────
# Three entries lived in docs/patterns/error_ledger.md's "Pending import"
# section after the structured store's branch cut. Wave 6 closed 15/15
# (audit at docs/fable_roadmap/audits/2026-07-08-wave6-close.md) and the
# follow-up tasks for all three merged, so the import marks them fixed
# with a note pointing at the resolving task / wave.  Idempotent on
# ``source_id`` — re-running the import is a no-op (see
# :func:`import_pending_ledger_entries`).
#
# Sources map to the closest live taxonomy bucket:
# - conflict-flag (task 225): merge ladder gap → merge_failure.
# - zombie sandboxes (tasks 231/232 → task 235): reconciler wedge on
#   pid-based liveness → failure_tagger (unclassified infra signal at
#   capture time).
# - watcher clock jump: false stall alarms after host sleep → failure_tagger.

_PENDING_ENTRIES = (
    {
        "source": ErrorEvent.SOURCE_MERGE_FAILURE,
        "symptom": (
            "Human guidance comment ignored on a merge conflict (task 225): "
            "migration-collision gate parks with merge_status=conflict but "
            "reply-resume signal listens only for needs_human — two flags "
            "for one meaning."
        ),
        "source_id": "pending:conflict-flag-225",
        "disposition": ErrorEvent.DISPOSITION_FIXED,
        "disposition_note": "fixed by W6.11 (task 231: needs_human unifies the flag)",
        "context": {
            "wave": "W6.11",
            "fix_task": "231",
            "doc_label": "task 225",
        },
    },
    {
        "source": ErrorEvent.SOURCE_FAILURE_TAGGER,
        "symptom": (
            "Zombie sandboxes survived host sleep with dead agents inside "
            "(tasks 231/232: process alive, trace 188m silent); reconciler "
            "liveness was pid existence so kill-and-requeue never fired."
        ),
        "source_id": "pending:zombie-sandboxes-235",
        "disposition": ErrorEvent.DISPOSITION_FIXED,
        "disposition_note": "fixed by W6.15 (task 235: progress-based liveness)",
        "context": {
            "wave": "W6.15",
            "fix_task": "235",
            "doc_label": "tasks 231/232",
        },
    },
    {
        "source": ErrorEvent.SOURCE_FAILURE_TAGGER,
        "symptom": (
            "Watcher stall clocks lied after host sleep (false 250m alarms): "
            "wall-clock jump across sleep crossed the threshold falsely."
        ),
        "source_id": "pending:watcher-clock-jump",
        "disposition": ErrorEvent.DISPOSITION_FIXED,
        "disposition_note": "fixed: watch_board.sh CLOCK_JUMP reset",
        "context": {
            "doc_label": "in-session fix during wave 6 close",
        },
    },
    {
        "source": ErrorEvent.SOURCE_FAILURE_TAGGER,
        "symptom": (
            "Backend tests in a branch worktree imported the main checkout's "
            "odin (pipx/venv editable install) instead of the worktree's, "
            "surfacing phantom TypeErrors (e.g. MergeResult unexpected "
            "keyword) for ~20 min before version skew was suspected."
        ),
        "source_id": "pending:odin-version-skew-241",
        "disposition": ErrorEvent.DISPOSITION_FIXED,
        "disposition_note": (
            "fixed by W7 (task 241): scripts/verify.sh prepends this tree's "
            "odin/src to PYTHONPATH; tests/test_odin_import_skew.py guards it"
        ),
        "context": {
            "wave": "W7",
            "fix_task": "241",
            "doc_label": "operator-observed, task 241",
        },
    },
)


def import_pending_ledger_entries() -> int:
    """Import the late W6 retrospective entries into ErrorEvent.

    Each entry is keyed by a stable ``source_id`` (``pending:<slug>``)
    so a re-run finds the existing row by ``(source, source_id)`` and
    skips it without creating a duplicate. Existing rows — including
    any operator-written ``disposition_note`` — are left untouched: the
    helper only writes its own note on first import, so a manual
    override after a re-import stays intact.

    Returns the count of newly-created rows (0 when the import is a
    no-op).  Idempotent by construction: a third call still adds zero
    rows.
    """
    created = 0
    for entry in _PENDING_ENTRIES:
        existing = ErrorEvent.objects.filter(
            source=entry["source"],
            source_id=entry["source_id"],
        ).first()
        if existing is not None:
            continue
        ErrorEvent.objects.create(
            source=entry["source"],
            source_id=entry["source_id"],
            symptom=entry["symptom"][:SYMPTOM_LIMIT],
            symptom_signature=symptom_signature(entry["symptom"]),
            context=entry["context"],
            disposition=entry["disposition"],
            disposition_note=entry["disposition_note"],
            failure_class=entry.get("failure_class", ""),
        )
        created += 1
    return created


# Public re-export of DISPOSITION_CHOICES for callers that want the
# canonical choice list without reaching into the model.
DISPOSITION_CHOICES = ErrorEvent.DISPOSITION_CHOICES


def serialize_error_event(evt: ErrorEvent) -> Dict:
    """Flat dict for API / JSON output."""
    return {
        "id": evt.id,
        "source": evt.source,
        "source_id": evt.source_id,
        "symptom": evt.symptom,
        "symptom_signature": evt.symptom_signature,
        "failure_class": evt.failure_class,
        "task_id": evt.task_id,
        "spec_id": evt.spec_id,
        "log_path": evt.log_path,
        "log_tail": evt.log_tail,
        "context": evt.context or {},
        "disposition": evt.disposition,
        "disposition_note": evt.disposition_note,
        "created_at": evt.created_at.isoformat() if evt.created_at else None,
        "updated_at": evt.updated_at.isoformat() if evt.updated_at else None,
    }