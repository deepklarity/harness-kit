"""Memory: turn twins into a quote (estimate) and stamp estimate vs actual.

Builds on tasks/similarity.py — the twins a task scored at dispatch are the
sample we quote from. The quote is a robust aggregate (median) of the
twins' tokens and duration, with a stated confidence tier by twin count:

    0 twins  → confidence "none"   →  "no estimate"  (never invented)
    1 twin   → confidence "low"
    2 twins  → confidence "medium"
    3+ twins → confidence "high"

A task's quote at dispatch lives in `task.metadata["estimate"]`. Its actual
cost at DONE / auto-promotion (TESTING) lives in `task.metadata["actual"]`.
quote_accuracy (testing_tools/quote_accuracy.py) reads both for an audit
number across the whole board.

Why median (not mean): the kit already saw a single 3M-token outlier pull
mean estimates 10x over real cost. Median survives that and matches how
contractors actually quote — "based on the few recent jobs that looked
like this one." Twin count sets the confidence tier; median sets the
number.
"""

from __future__ import annotations

import statistics
from typing import Iterable, List, Optional

CONFIDENCE_NONE = "none"
CONFIDENCE_LOW = "low"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_HIGH = "high"


def _confidence_for(twin_count: int) -> str:
    """Confidence tier by twin count. 0 → none; never "high" with 1-2 twins."""
    if twin_count <= 0:
        return CONFIDENCE_NONE
    if twin_count == 1:
        return CONFIDENCE_LOW
    if twin_count == 2:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_HIGH


def _median_or_none(values: Iterable) -> Optional[float]:
    """Robust median that drops None and returns None if nothing remains."""
    cleaned = [v for v in values if v is not None]
    if not cleaned:
        return None
    # statistics.median returns float for even-length lists (mean of middle two)
    return float(statistics.median(cleaned))


def compute_estimate(twins: List[dict]) -> dict:
    """Aggregate twins into a quote.

    Returns a dict shaped:
      - 0 twins: {"confidence": "none", "twin_count": 0}
      - N≥1 twins: {confidence, twin_count, tokens_median, duration_ms_median,
                     source_twin_ids}

    Median is over non-None values; if every twin is missing a metric, the
    median for that metric is None (not 0) — never invent a number.
    """
    twins = list(twins or [])
    twin_count = len(twins)
    confidence = _confidence_for(twin_count)
    if twin_count == 0:
        return {"confidence": confidence, "twin_count": 0}

    return {
        "confidence": confidence,
        "twin_count": twin_count,
        "tokens_median": _median_or_none(t.get("tokens") for t in twins),
        "duration_ms_median": _median_or_none(t.get("duration_ms") for t in twins),
        "source_twin_ids": [t["task_id"] for t in twins if t.get("task_id") is not None],
    }


# ── Formatting helpers ────────────────────────────────────────────

def _format_duration(minutes: float) -> str:
    if minutes < 1:
        return f"{minutes * 60:.0f}s"
    if minutes < 60:
        # 1 decimal under 10, whole under 60. Reads better in a one-liner.
        return f"{minutes:.1f} min" if minutes < 10 else f"{minutes:.0f} min"
    hours = minutes / 60
    return f"{hours:.1f} h"


def _format_tokens(n) -> str:
    """Compact human form: 1.2K / 2.5M / 750."""
    if n is None:
        return "—"
    n = float(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{int(n)}"


def format_estimate_line(estimate: dict) -> str:
    """Render one human line for the dispatch comment.

    No twins → "Estimate: no estimate (no twins on this board yet)".
    Otherwise: "Estimate: ~15 min, ~2.5M tokens (high, 3 twins)" with
    whichever of tokens / duration is available.
    """
    if estimate.get("confidence") == CONFIDENCE_NONE:
        return "Estimate: no estimate (no twins on this board yet)"

    parts = []
    dur_ms = estimate.get("duration_ms_median")
    if dur_ms is not None:
        parts.append(f"~{_format_duration(dur_ms / 60_000)}")
    tok = estimate.get("tokens_median")
    if tok is not None:
        parts.append(f"~{_format_tokens(tok)} tokens")

    body = ", ".join(parts) if parts else "insufficient twin data"
    twin_count = estimate.get("twin_count", 0)
    twin_word = "twin" if twin_count == 1 else "twins"
    return f"Estimate: {body} ({estimate['confidence']}, {twin_count} {twin_word})"


def format_actual_line(estimate: dict, actual: dict) -> str:
    """Render the one-line 'Estimate: X → Actual: Y' trail appended on completion."""
    est_text = format_estimate_line(estimate)
    parts = []
    tok = actual.get("tokens")
    if tok is not None:
        parts.append(f"~{_format_tokens(tok)} tokens")
    dur_ms = actual.get("duration_ms")
    if dur_ms is not None:
        parts.append(f"~{_format_duration(dur_ms / 60_000)}")
    body = ", ".join(parts) if parts else "insufficient data"
    return f"Actual: {body}  (was: {est_text})"


# ── Metadata stamping ─────────────────────────────────────────────

# Key under which the dispatch-time quote lives on task.metadata
ESTIMATE_KEY = "estimate"
# Key under which the completion-time actual cost lives
ACTUAL_KEY = "actual"


def stamp_estimate(task, estimate: dict) -> dict:
    """Persist the dispatch-time quote on task.metadata.estimate.

    Best-effort: a metadata save failure must never break dispatch. The
    caller (similarity.post_twins_comment) is already inside a try/except.
    """
    metadata = dict(task.metadata or {})
    metadata[ESTIMATE_KEY] = estimate
    task.metadata = metadata
    task.save(update_fields=["metadata"])
    return metadata


def _build_actual_payload(task, transition: str) -> dict:
    """Read the actual tokens / duration from the task at completion time.

    Tokens come from the trace comment via compute_usage_from_trace (same
    authoritative source the API's usage field already uses). Duration comes
    from metadata.last_duration_ms (the field set by the executor summary
    path at the end of every run).
    """
    from .execution_processing import compute_usage_from_trace

    usage = compute_usage_from_trace(task) or {}
    total = usage.get("total_tokens")
    if total is None:
        inp = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
        out = usage.get("output_tokens") or usage.get("completion_tokens") or 0
        total = inp + out if (inp or out) else None
    metadata = task.metadata or {}
    return {
        "tokens": int(total) if total else None,
        "duration_ms": metadata.get("last_duration_ms"),
        "transition": transition,
    }


def stamp_actual(task, transition: str) -> Optional[dict]:
    """Persist the completion-time actual on task.metadata.actual.

    Returns the actual payload (or None if no estimate existed — there is
    nothing to compare against, so the stamp is a no-op).
    """
    metadata = dict(task.metadata or {})
    if ESTIMATE_KEY not in metadata:
        # No quote was ever stamped — nothing to compare to. Don't fabricate
        # an "actual" without an "estimate" to pair it with.
        return None
    actual = _build_actual_payload(task, transition)
    metadata[ACTUAL_KEY] = actual
    task.metadata = metadata
    task.save(update_fields=["metadata"])
    return actual


# ── Comment trail ─────────────────────────────────────────────────

def append_quote_trail_to_comment(task, estimate: dict, actual: dict) -> Optional[int]:
    """Append one 'Actual: … (was: Estimate …)' line to the dispatch comment.

    The 'telemetry comment' for an estimate-vs-actual trail is the dispatch
    twins comment itself (author_label='memory') — that is the comment that
    posted the quote originally, so the trail naturally lives there. If no
    memory comment exists (rare: a task that was never dispatched, or one
    whose dispatch predates the feature), we silently skip — never invent
    a comment just to host the line.
    """
    from .models import TaskComment

    memory_comments = TaskComment.objects.filter(task=task, author_label="memory")
    target = memory_comments.order_by("-created_at").first()
    if target is None:
        return None

    new_line = format_actual_line(estimate, actual)
    if new_line in target.content:
        # Idempotent — already trailed.
        return target.id

    target.content = f"{target.content.rstrip()}\n{new_line}"
    target.save(update_fields=["content"])
    return target.id


def stamp_actual_and_trail(task, transition: str) -> Optional[dict]:
    """Stamp `actual` on the task and append the trail to the dispatch comment.

    Single entry point used by every DONE / auto-promotion path so they
    can't drift apart. Returns the actual payload (or None if no estimate
    was on file).
    """
    metadata = task.metadata or {}
    estimate = metadata.get(ESTIMATE_KEY)
    if not estimate:
        return None
    actual = stamp_actual(task, transition)
    if actual is None:
        return None
    # Refresh from DB so the append uses the just-saved comment state.
    task.refresh_from_db(fields=["metadata"])
    append_quote_trail_to_comment(task, estimate, actual)
    return actual