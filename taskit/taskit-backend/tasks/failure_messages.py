"""Failure messages: the single source of truth for what a human reads.

The failure page shows three things next to a FAILED task:

  1. The banner — a plain-English sentence describing what happened.
  2. The metadata — machine fields (type, origin, class) for the trace
     viewer and downstream routing. Stamped on every FAILED transition.
  3. The suggested action — one honest sentence about the next step.

All three are derived from the failure_class, which the tagger
(:mod:`tasks.failure_tagger`) stamps at every FAILED transition. This
module owns the *language* and the *next-step* mapping; the policy
table (:mod:`tasks.failure_policy`) owns the *automatic dispatch*; the
two are kept side-by-side so they cannot drift apart.

Design rules (task #359)
------------------------

* **One sentence each.** No key:value noise, no log-style brackets, no
  system fingerprint tokens. Every line passes the read-it-aloud test
  from ``docs/fable_roadmap/TONE.md``.
* **Honest next step.** A review failure suggests reading the reviewer's
  finding; a quota failure suggests reassignment; a crash suggests
  investigation. We never lie — "Requeue to re-dispatch" was the wrong
  answer on the 3-strike cap and produced the banner lie on task #356.
* **Single source of truth.** The frontend reads ``failure_suggested_action``
  and ``failure_human_reason`` off the serializer; the serializer reads
  them off this module; every place that puts a task in FAILED calls
  :func:`write_failure_metadata` so the metadata stays in sync with
  the language.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional


# ── Suggested actions ───────────────────────────────────────────────────
#
# One short, honest sentence per failure class. The frontend renders this
# verbatim in the FAILED region's "Next step" line and in the failure
# banner's footer. Words were chosen so a person who has never seen the
# failure before knows what to do.

SUGGESTED_ACTIONS: dict[str, str] = {
    # ── Transient infra — the system can usually clear it itself ────────
    "stale_execution":
        "The worker that was running this task disappeared. Retry it.",
    "sandbox_unavailable":
        "The sandbox the agent runs in never started. Retry it.",
    "truncation":
        "The agent's response was cut off mid-stream. Retry it.",
    "silent_hang":
        "The agent stopped producing output with no explanation. Retry it.",
    "transport_error":
        "The connection to the model broke. Wait a moment and retry it.",
    "lock_race":
        "The database was briefly busy. Retry it.",
    "error_loop":
        "The provider hit a retry loop. Wait for the cooldown window, then retry it.",

    # ── Quota / model availability — reassign, don't requeue ────────────
    "quota_exhaustion":
        "This provider is out of quota. Reassign to a different agent.",
    "model_unavailable":
        "This model is not available right now. Pick a different model.",

    # ── Capability / review — needs human judgment ──────────────────────
    "review_cap":
        "The reviewer rejected this work three times in a row. Read the latest reviewer's note and decide whether to change direction or send back with new guidance.",

    # ── Real failures — operator must fix something ─────────────────────
    "env_missing":
        "An API key or login is missing. Fix the environment, then retry it.",
    "auth_failure":
        "An API key or login is missing. Fix the environment, then retry it.",
    "timeout":
        "The task ran longer than its time limit. Look at the log to see how far it got, then decide whether to retry with a bigger budget.",
    "worktree_isolation":
        "The task has no worktree to run in. Either add it to a spec with a branch, or enable project-root execution on the board.",
    "disk_exhaustion":
        "The disk is full. Free up space, then retry it.",
    "crash":
        "The agent process crashed. Look at the log, fix the cause, then retry it.",
    "cancelled":
        "You stopped this run yourself. Requeue it when you're ready.",
    "unknown":
        "The system couldn't classify this failure. Look at the log and decide.",
}


# Fallback for any class missing from the table above — never auto-retry,
# never silently re-dispatch. The frontend's fallback path uses this.
DEFAULT_SUGGESTED_ACTION = SUGGESTED_ACTIONS["unknown"]


# ── Failure-type → suggested-action aliases ──────────────────────────────
#
# The legacy `last_failure_type` field is finer-grained than the failure
# taxonomy (`backend_auth_failure`, `agent_execution_failure`,
# `internal_error`, `cancelled`, `timeout`, `missing_worktree`,
# `stale_execution`, etc.). The action language should still be
# class-aligned, so this map collapses type values onto the class-level
# action. The serializer uses it to render the banner before the
# tagger has had a chance to stamp `failure_class`.

_FAILURE_TYPE_TO_CLASS: dict[str, str] = {
    "backend_auth_failure": "env_missing",
    "cli_not_found": "env_missing",
    "agent_execution_failure": "crash",
    "internal_error": "crash",
    "backend_exception": "crash",
    "cancelled": "cancelled",
    "timeout": "timeout",
    "missing_worktree": "worktree_isolation",
    "stale_execution": "stale_execution",
    "llm_call_failure": "quota_exhaustion",
    "model_escalation_failure": "model_unavailable",
}


# ── Read-side helpers (consumed by the serializer + frontend) ──────────


def suggested_action_for_metadata(metadata: Mapping[str, Any] | None) -> str:
    """Return the honest next-step sentence for a task's failure metadata.

    Prefers ``failure_class`` (the taxonomy class the tagger stamped);
    falls back to ``last_failure_type`` (the finer-grained legacy
    signal); finally to :data:`DEFAULT_SUGGESTED_ACTION` when nothing is
    known. Never raises, never returns an empty string — the banner
    renders the result verbatim.
    """
    md = metadata or {}
    cls = (md.get("failure_class") or "").strip().lower()
    if cls and cls in SUGGESTED_ACTIONS:
        return SUGGESTED_ACTIONS[cls]

    ftype = (md.get("last_failure_type") or "").strip().lower()
    if ftype:
        mapped = _FAILURE_TYPE_TO_CLASS.get(ftype)
        if mapped and mapped in SUGGESTED_ACTIONS:
            return SUGGESTED_ACTIONS[mapped]

    return DEFAULT_SUGGESTED_ACTION


# ── Banner sentence formatter ────────────────────────────────────────────
#
# `humanize_failure_reason` turns the raw `last_failure_reason` blob (often
# a stack trace excerpt, a systemd error, an `Origin: taskit_dag_executor`
# tag) into a sentence a person can say out loud. It is the user-facing
# companion to `format_reason_for_humans` — same goal, two entry points.

_LOG_LINE_PREFIXES: tuple[str, ...] = (
    "Failure type:",
    "Failure type ",
    "Reason:",
    "Origin:",
    "Debug:",
    "Reason ",
)


def _strip_log_lines(text: str) -> str:
    """Drop the key:value lines and bracketed origin stamps that older
    call sites stamp into `last_failure_reason`. Returns the remainder
    collapsed to a single sentence."""
    if not text:
        return ""
    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Drop lines that look like the legacy FAILED comment shape.
        if any(line.startswith(p) for p in _LOG_LINE_PREFIXES):
            continue
        # Drop bracketed origin stamps like "[taskit_dag_executor]".
        if line.startswith("[") and line.endswith("]") and len(line) < 80:
            continue
        cleaned_lines.append(line)
    return " ".join(cleaned_lines).strip()


def format_reason_for_humans(raw_reason: str) -> str:
    """Strip log-style key:value noise from a raw reason string.

    The output is the first pass at the banner sentence — call sites
    that have richer context (failure_class, last_failure_type) should
    use :func:`humanize_failure_reason` instead, which composes a fresh
    sentence from the class.
    """
    return _strip_log_lines(raw_reason or "")


# Human-readable prefixes keyed by failure_class. Composed with the raw
# reason so the banner always leads with *what happened* in plain words.
# When the class is missing we fall through to the raw reason alone.

_CLASS_OPENERS: dict[str, str] = {
    "stale_execution": "The worker that was running this task disappeared.",
    "sandbox_unavailable": "The agent never started inside its sandbox.",
    "truncation": "The agent's response was cut off before it finished.",
    "silent_hang": "The agent stopped producing output without finishing.",
    "transport_error": "The connection to the model broke mid-run.",
    "lock_race": "The database was briefly locked and the run gave up.",
    "error_loop": "The provider kept retrying and never made progress.",
    "quota_exhaustion": "This provider ran out of quota.",
    "model_unavailable": "The model on this task is not available right now.",
    "review_cap": "The reviewer rejected this work three times in a row.",
    "env_missing": "An API key or login is missing on the worker.",
    "auth_failure": "An API key or login is missing on the worker.",
    "timeout": "The task ran longer than its time limit.",
    "worktree_isolation": "The task has no worktree to run in.",
    "disk_exhaustion": "The disk ran out of space.",
    "crash": "The agent process crashed.",
    "cancelled": "The run was stopped by hand.",
    "unknown": "The system could not classify this failure.",
}


def humanize_failure_reason(
    raw_reason: str,
    *,
    failure_type: Optional[str] = None,
    failure_class: Optional[str] = None,
    failure_origin: Optional[str] = None,
) -> str:
    """Compose a one-sentence banner line for the failure reason.

    Prefers the class (cleaner, taxonomy-aligned) over the raw reason.
    When the class is known, the opener is the class sentence and the
    raw reason is appended *only* if it adds detail the opener doesn't
    already cover — a duplicate sentence is worse than a short one.

    Always returns a non-empty string; the legacy "Unknown error — check
    task comments for details" placeholder is only used when both the
    raw reason and the class are missing.
    """
    opener = ""
    cls = (failure_class or "").strip().lower()
    if cls and cls in _CLASS_OPENERS:
        opener = _CLASS_OPENERS[cls]
    else:
        ftype = (failure_type or "").strip().lower()
        mapped_cls = _FAILURE_TYPE_TO_CLASS.get(ftype)
        if mapped_cls and mapped_cls in _CLASS_OPENERS:
            opener = _CLASS_OPENERS[mapped_cls]

    cleaned = _strip_log_lines(raw_reason or "")
    if opener and cleaned:
        # Avoid duplicating the opener's content verbatim.
        opener_first_words = " ".join(opener.lower().split()[:5])
        cleaned_lower = cleaned.lower()
        if opener_first_words and opener_first_words in cleaned_lower:
            return cleaned
        return f"{opener} {cleaned}"
    if opener:
        return opener
    if cleaned:
        return cleaned
    return "Unknown error — check task comments for details."


# ── Write-side helper (consumed at every FAILED transition) ─────────────


def write_failure_metadata(
    metadata: dict,
    *,
    failure_type: str,
    failure_reason: str,
    failure_origin: str,
    failure_class: Optional[str] = None,
) -> str:
    """Stamp the failure metadata block in-place and return the class.

    This is the single place every FAILED transition should call — the
    dispatch gate, the execution fallback, the stale-recovery reaper,
    the reflection cap, and the cancellation path all go through here
    so the banner, the inbox, the failure-policy audit, and the
    reminders read a consistent metadata shape.

    Parameters
    ----------
    metadata:
        The task's existing ``metadata`` dict; mutated in place.
    failure_type, failure_reason, failure_origin:
        The three machine fields every FAILED transition owes the next
        reader. ``failure_origin`` distinguishes ``taskit_dag_executor``
        (worker-side) from ``taskit_views`` (reflection-cap side) so a
        trace viewer can route the right logs.
    failure_class:
        Optional explicit class. When ``None`` (the common case), the
        tagger classifies from the type/reason pair so the policy
        engine can find the entry in
        :data:`tasks.failure_policy.DEFAULT_POLICY_TABLE`. When provided,
        it is stamped directly — the only case where a detector knows
        the class better than the tagger (the review-cap path is one
        such case).

    Returns
    -------
    str
        The failure_class that was stamped, useful for the caller's
        audit comment.
    """
    metadata["last_failure_type"] = failure_type
    metadata["last_failure_reason"] = failure_reason
    metadata["last_failure_origin"] = failure_origin

    if failure_class:
        metadata["failure_class"] = failure_class
    else:
        # Lazy import: the tagger imports from failure_messages for the
        # taxonomy constants in some flows — keep the dependency
        # direction one-way.
        from .failure_tagger import tag_failure_class
        tag_failure_class(metadata)

    return metadata.get("failure_class") or "unknown"