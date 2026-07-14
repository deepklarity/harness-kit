"""Mistakes ledger (task #223).

Every rejected review (reflection NEEDS_WORK/FAIL) and every task-FAILED
transition is distilled to one line in a MistakeEntry. The wave-5 twins
comment then surfaces matching warnings on similar future tasks: "tasks
like this failed on X before — check it first."

The distillation is rule-based — no extra model call. The one-liner is the
reviewer's own summary (for reflections) or the recorded failure reason (for
executions), trimmed to a single clause. ``failure_class`` reuses the
deterministic signatures in ``failure_tagger`` where one matches; otherwise
it is left blank (a code-quality rework has no meaningful infra class).

W6.3 (task #225): each entry also carries a deterministic
``fingerprint`` — ``<salient> / <provider> / <stage>`` — that the
failure-policy layer uses to ask "has this exact shape self-healed
before?".  See :mod:`tasks.fingerprints` for the lookup and advice
math.

Dedup is per (task, source, source_id): one entry per reflection report or
per execution run. Re-processing the same event (idempotent re-runs,
duplicate callbacks) never double-records.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from .failure_tagger import classify_failure_text
from .fingerprints import (
    STAGE_EXECUTION,
    STAGE_REFLECTION,
    compute_fingerprint,
)
from .models import MistakeEntry

# A distilled line should read as one glanceable clause. 140 chars covers a
# full sentence without wrapping awkwardly in a twins comment continuation.
ONE_LINER_LIMIT = 140


def _first_clause(text: Optional[str], limit: int = ONE_LINER_LIMIT) -> str:
    """Reduce *text* to its first clause: first line, trimmed to a sentence
    boundary if one falls inside the limit, else hard-truncated with '...'."""
    text = (text or "").strip()
    if not text:
        return ""
    first_line = text.split("\n", 1)[0].strip()
    for sep in (". ", "! ", "? ", "; "):
        idx = first_line.find(sep)
        if 0 < idx < limit:
            return first_line[: idx + 1].strip()
    if len(first_line) <= limit:
        return first_line
    return first_line[: limit - 3].rstrip() + "..."


def _reflection_text(report) -> str:
    """Concatenate every free-text analysis field a reviewer fills in."""
    return " ".join(
        p for p in (
            getattr(report, "verdict_summary", "") or "",
            getattr(report, "quality_assessment", "") or "",
            getattr(report, "slop_detection", "") or "",
            getattr(report, "improvements", "") or "",
            getattr(report, "quota_failure", "") or "",
        ) if p
    ).strip()


def distill_reflection(report) -> tuple:
    """One-line distillation of a NEEDS_WORK/FAIL reflection verdict.

    Returns ``(one_liner, failure_class)``. Prefers the verdict summary;
    falls back to the first analysis section with content, then to a
    bare verdict label. ``failure_class`` is blank when no known
    signature matches (the common case for plain code-quality rework).
    """
    verdict = (getattr(report, "verdict", "") or "").upper()
    one_liner = _first_clause(getattr(report, "verdict_summary", ""))
    if not one_liner:
        for field in ("quality_assessment", "slop_detection", "improvements"):
            clause = _first_clause(getattr(report, field, ""))
            if clause:
                one_liner = clause
                break
    if not one_liner:
        label = verdict or "NEEDS_WORK"
        one_liner = f"{label} verdict (no detail recorded)"

    failure_class = classify_failure_text(_reflection_text(report))
    if failure_class == "unknown":
        failure_class = ""
    return one_liner, failure_class


def distill_execution(task) -> tuple:
    """One-line distillation of a task-FAILED transition.

    Returns ``(one_liner, failure_class)``. The one-liner is the recorded
    failure reason; ``failure_class`` is whatever ``failure_tagger`` already
    stamped on ``metadata.failure_class`` at the FAILED transition.
    """
    metadata = task.metadata or {}
    ftype = (metadata.get("last_failure_type") or "").strip()
    freason = (metadata.get("last_failure_reason") or "").strip()
    one_liner = _first_clause(freason) or (ftype.replace("_", " ") if ftype else "Execution failed")
    failure_class = metadata.get("failure_class") or ""
    return one_liner, failure_class


def record_reflection_mistake(report) -> MistakeEntry:
    """Record (idempotently) one ledger line for a NEEDS_WORK/FAIL reflection.

    Dedup key: (task, "reflection", report.id). Re-completing the same
    reflection report never duplicates the entry.
    """
    task = report.task
    one_liner, failure_class = distill_reflection(report)
    verdict = (getattr(report, "verdict", "") or "").upper()
    reason = _reflection_text(report)
    fingerprint = compute_fingerprint(
        failure_class=failure_class,
        reason=reason,
        agent=task.assignee.name if task.assignee_id else "",
        model=task.model_name or "",
        stage=STAGE_REFLECTION,
    )
    obj, _ = MistakeEntry.objects.get_or_create(
        task=task,
        source=MistakeEntry.SOURCE_REFLECTION,
        source_id=str(report.id),
        defaults={
            "spec": task.spec,
            "agent": task.assignee.name if task.assignee_id else "",
            "model": task.model_name or "",
            "one_liner": one_liner,
            "failure_class": failure_class,
            "verdict": verdict,
            "fingerprint": fingerprint,
        },
    )
    return obj


def record_execution_mistake(task, *, run_token: str = "") -> MistakeEntry:
    """Record (idempotently) one ledger line for a task-FAILED transition.

    Dedup key: (task, "execution", run_token). A second FAILED for the same
    run never duplicates; a later run (new run_token) gets its own entry.
    """
    metadata = task.metadata or {}
    one_liner, failure_class = distill_execution(task)
    fingerprint = compute_fingerprint(
        failure_class=failure_class,
        reason=metadata.get("last_failure_reason", ""),
        agent=task.assignee.name if task.assignee_id else "",
        model=task.model_name or "",
        stage=STAGE_EXECUTION,
    )
    obj, _ = MistakeEntry.objects.get_or_create(
        task=task,
        source=MistakeEntry.SOURCE_EXECUTION,
        source_id=run_token or "",
        defaults={
            "spec": task.spec,
            "agent": task.assignee.name if task.assignee_id else "",
            "model": task.model_name or "",
            "one_liner": one_liner,
            "failure_class": failure_class,
            "verdict": "",
            "fingerprint": fingerprint,
        },
    )
    return obj


def latest_mistake_for_tasks(task_ids: Iterable) -> Dict[int, MistakeEntry]:
    """Batch-load the most recent mistake per task (for twins warnings).

    Returns ``{task_id: MistakeEntry}``. Used by the similarity service so a
    twins comment can surface each twin's latest failure without an N+1.
    """
    ids = [t for t in task_ids if t is not None]
    if not ids:
        return {}
    rows = (
        MistakeEntry.objects.filter(task_id__in=ids)
        .order_by("task_id", "-created_at", "-id")
    )
    latest: Dict[int, MistakeEntry] = {}
    for entry in rows:
        # First row per task_id wins (most recent, due to the ordering).
        latest.setdefault(entry.task_id, entry)
    return latest


def mistakes_for_spec(spec_id) -> List[MistakeEntry]:
    """All ledger lines for a spec, newest first."""
    return list(MistakeEntry.objects.filter(spec_id=spec_id).order_by("-created_at", "-id"))


def serialize_mistake(entry: MistakeEntry) -> dict:
    """Flat dict for CLI / JSON output."""
    return {
        "id": entry.id,
        "task_id": entry.task_id,
        "spec_id": entry.spec_id,
        "source": entry.source,
        "source_id": entry.source_id,
        "agent": entry.agent,
        "model": entry.model,
        "one_liner": entry.one_liner,
        "failure_class": entry.failure_class,
        "verdict": entry.verdict,
        "fingerprint": entry.fingerprint,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


def warning_for_twin(entry: Optional[MistakeEntry]) -> Optional[dict]:
    """Shape a twin's mistake into the ``warning`` payload for the twins comment.

    Returns None when there is no mistake (clean twin) — the comment then
    renders no warning line for that twin.
    """
    if entry is None:
        return None
    return {
        "one_liner": entry.one_liner,
        "failure_class": entry.failure_class,
        "source": entry.source,
    }
