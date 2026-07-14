"""Failure fingerprints (task #225 / W6.3).

Every failure ships with a short deterministic signature — a fingerprint —
that lets the dispatcher recognise it as the same kind of failure it has
seen before.  The signature is shaped to match what an operator would say
out loud when describing the failure:

    "<salient> / <provider> / <stage>"

e.g. ``"401 authentication_failed / claude / execution"`` or
``"no_output / glm / reflection"``.  Same failure, same fingerprint.
Different providers / classes / stages get different fingerprints.

What it buys us
---------------

1. **MistakeEntry lookup** — the indexed ``fingerprint`` column on every
   stored ledger line lets us ask "how many times have we seen this exact
   shape?" in O(1) rather than re-text-mining.
2. **Triage advice** — the wave-4 failure-policy audit comment gains a
   "seen N times before; last resolution: <one-liner>; safe action:
   <requeue|hold|escalate>" line, derived from the eventual outcome of
   prior entries with the same fingerprint.  An operator (human or
   agent) reading the comment knows at a glance what to try.
3. **Auto-requeue override** — when the history overwhelmingly says "this
   fingerprint never self-heals", the static ``AUTO_REQUEUE`` policy is
   downgraded to ``HUMAN`` for this occurrence, with the history quoted.
   The mirror ("always works on retry") lets us skip the policy's
   backoff delay.

Why deterministic
-----------------

Failure class (from ``failure_tagger``) is already deterministic — adding
fingerprint logic that *also* requires an LLM would defeat the point.  All
rules here are string normalisation: lowercased, alpha-only, with a small
per-class label table for salient substrings.  Unrecognised input
collapses to ``"unclassified"`` — never guess.

Dedup
-----

Same fingerprint from two genuinely different failures is acceptable: the
history rollup will smooth the noise, and the failure class on each entry
already distinguishes real from accidental collisions.  The brief
explicitly calls out a *second* occurrence quoting the first, so a strict
fingerprint is the design target.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional


# ── Stage constants ────────────────────────────────────────────────
# A fingerprint's stage distinguishes execution-time failure from
# reflection-time failure — same salient + provider on different stages
# is a different fingerprint (different remediation path).

STAGE_EXECUTION = "execution"
STAGE_REFLECTION = "reflection"

STAGES = frozenset({STAGE_EXECUTION, STAGE_REFLECTION})


# ── Safe-action recommendation constants ───────────────────────────
# Three discrete recommendations, matching the failure_policy actions
# one-to-one so the audit comment can compare directly.

ACTION_REQUEUE = "requeue"     # history says auto-requeue always worked
ACTION_HOLD = "hold"           # mixed history — keep stock policy
ACTION_ESCALATE = "escalate"   # history overwhelmingly needed a human


# ── Salient-label lookup tables ────────────────────────────────────
# Each entry maps failure_class → list of (substring, label) precedence
# rules.  The FIRST substring found in the lowercased reason wins; if
# none match, the class default label is used.  These are the canonical
# salient tokens the brief asks for ("401 authentication_failed",
# "no_output", "truncated_mid_generation", ...).

# Order matters: more specific patterns first.  The class default is the
# last token in each list.

_SALIENT_TABLE: Dict[str, tuple] = {
    "env_missing": (
        ("401", "401 authentication_failed"),
        ("authentication failed", "401 authentication_failed"),
        ("unauthorized", "401 authentication_failed"),
        ("auth error", "401 authentication_failed"),
        ("api_key", "api_key_missing"),
        ("api key", "api_key_missing"),
        ("command not found", "cli_not_found"),
        ("not found on path", "cli_not_found"),
        ("ineligible tier", "ineligible_tier"),
        ("ineligibletier", "ineligible_tier"),
        ("env_missing", "env_missing"),  # default
    ),
    "quota_exhaustion": (
        ("429", "429_quota_exhausted"),
        ("too many requests", "429_quota_exhausted"),
        ("rate limit", "rate_limited"),
        ("quota", "quota_exhausted"),
        ("quota_exhaustion", "quota_exhausted"),  # default
    ),
    "truncation": (
        ("truncated mid-generation", "truncated_mid_generation"),
        ("response truncated", "truncated_mid_generation"),
        ("output truncated", "truncated_mid_generation"),
        ("max tokens", "max_tokens_reached"),
        ("output cap", "output_cap_reached"),
        ("truncation", "truncated_mid_generation"),  # default
    ),
    "silent_hang": (
        ("produced no output", "no_output"),
        ("terminated silently", "terminated_silently"),
        ("did not emit odin-status", "no_odin_status"),
        ("silent_hang", "no_output"),  # default
    ),
    "transport_error": (
        ("remote protocol error", "remote_protocol_error"),
        ("remoteprotocolerror", "remote_protocol_error"),
        ("connection reset", "connection_reset"),
        ("connection refused", "connection_refused"),
        ("chunked encoding", "remote_protocol_error"),
        ("tls", "tls_error"),
        ("transport_error", "remote_protocol_error"),  # default
    ),
    "timeout": (
        ("deadline exceeded", "deadline_exceeded"),
        ("timed out", "deadline_exceeded"),
        ("timeout", "deadline_exceeded"),  # default
    ),
    "stale_execution": (
        ("worker died", "worker_died"),
        ("no odin runner", "no_runner"),
        ("process died", "worker_died"),
        ("stale_execution", "worker_died"),  # default
    ),
    "lock_race": (
        ("database is locked", "database_locked"),
        ("database locked", "database_locked"),
        ("lock_race", "database_locked"),  # default
    ),
    "disk_exhaustion": (
        ("no space left", "no_space_left"),
        ("enospc", "no_space_left"),
        ("disk_exhaustion", "no_space_left"),  # default
    ),
    "worktree_isolation": (
        ("worktree", "worktree_missing"),
        ("worktree_isolation", "worktree_missing"),  # default
    ),
    "crash": (
        ("import error", "import_error"),
        ("importerror", "import_error"),
        ("exception", "unhandled_exception"),
        ("crash", "unhandled_exception"),  # default
    ),
    "cancelled": (
        ("user", "user_cancelled"),
        ("cancelled", "user_cancelled"),  # default
    ),
    "model_unavailable": (
        ("not supported", "model_unsupported"),
        ("invalid request", "invalid_request"),
        ("model_unavailable", "model_unsupported"),  # default
    ),
    "error_loop": (
        # Task #262 — the reconciler's trace-tail detector.  The salient
        # mirrors the underlying provider class so a loop and a one-shot
        # failure on the same provider share a signature in the league.
        ("quota", "quota_loop"),
        ("rate limit", "quota_loop"),
        ("429", "quota_loop"),
        ("too many requests", "quota_loop"),
        ("stream", "stream_error_loop"),
        ("connection", "connection_loop"),
        ("transport", "stream_error_loop"),
        ("error_loop", "error_loop"),  # default
    ),
    "unknown": (
        ("unknown", "unclassified"),  # default
    ),
}


def _derive_provider(*, agent: str = "", model: str = "") -> str:
    """Map agent/model to a canonical provider key.

    The provider key is the lowest-common-denominator grouping that the
    failure_policy cares about: which *backend family* dropped the call.
    Examples:

      * agent="claude", model="" → "claude"
      * model="claude-sonnet-4-5" → "claude"
      * model="zai-coding-plan/glm-5.2" → "glm"
      * model="minimax-coding-plan/MiniMax-M3" → "minimax"
      * everything missing → "unknown"

    The first hit wins; model_name is canonical when both are present.
    """
    for src in (model or "", agent or ""):
        s = (src or "").strip().lower()
        if not s:
            continue
        # Strip path prefix (``zai-coding-plan/glm-5.2`` → ``glm-5.2``)
        s = s.split("/", 1)[-1]
        # Find a known provider token first (so "glm-coding-plan/glm-5.2"
        # still becomes "glm" not "glm-coding").
        for tag in ("claude", "glm", "minimax"):
            if tag in s:
                return tag
        # Fallback to first alphabetic token.
        head = re.split(r"[^a-z]+", s, maxsplit=1)[0]
        if head:
            return head
    return "unknown"


def _salient_label(failure_class: str, reason: str) -> str:
    """Pick a canonical salient token for this failure_class + reason.

    Deterministic.  Unknown inputs collapse to ``"unclassified"`` for
    unknown classes, or to the class default for known classes.  Never
    guesses silently — every return value is one of the entries in
    :data:`_SALIENT_TABLE` (or the fallback ``"unclassified"``).
    """
    rules = _SALIENT_TABLE.get(failure_class) or _SALIENT_TABLE["unknown"]
    text = (reason or "").lower()
    for needle, label in rules:
        if needle in text:
            return label
    # Default = last entry in the list (kept consistent across classes).
    return rules[-1][1] if rules else "unclassified"


def compute_fingerprint(
    *,
    failure_class: str = "",
    reason: str = "",
    agent: str = "",
    model: str = "",
    stage: str = STAGE_EXECUTION,
) -> str:
    """Return the canonical fingerprint string for this failure shape.

    The format mirrors the brief's example:
    ``"<salient> / <provider> / <stage>"``.  All three components are
    lowercased + whitespace-trimmed so two callers producing the same
    shape always get the same string.

    Empty inputs still produce a stable fingerprint — they map to the
    ``"unclassified"`` salient and the ``"unknown"`` provider so an
    un-stamped failure doesn't silently collide with a real one.
    """
    salient = _salient_label((failure_class or "").strip().lower(), reason or "")
    provider = _derive_provider(agent=agent, model=model)
    stage = (stage or STAGE_EXECUTION).strip().lower()
    if stage not in STAGES:
        stage = STAGE_EXECUTION
    return f"{salient} / {provider} / {stage}"


# ── History matching + advice ─────────────────────────────────────


def matches_query(fingerprint: str):
    """Return the queryset of MistakeEntry rows that match *fingerprint*.

    Returns ``MistakeEntry.objects.none()`` if no fingerprint was given.
    Same-fingerprint rows across all specs / boards are returned — the
    advice is a global signal, not a per-board one.
    """
    # Lazy import — keeps tasks.fingerprints loadable without Django
    # (tests sometimes poke the helpers without the ORM).
    from .models import MistakeEntry

    fp = (fingerprint or "").strip().lower()
    if not fp:
        return MistakeEntry.objects.none()
    return (
        MistakeEntry.objects.filter(fingerprint=fp)
        .order_by("-created_at", "-id")
    )


def _resolved_status(entry) -> str:
    """Return the eventual task status for *entry* (DONE / TESTING /
    FAILED / IN_PROGRESS / REVIEW / ...).

    We map any non-failure terminal status to ``"resolved"`` for the
    purpose of advice math because a retry that ended in REVIEW or
    IN_PROGRESS is at least past the immediate failure event.
    """
    task = getattr(entry, "task", None)
    if task is None:
        return "unknown"
    status = (task.status or "").upper()
    if status in ("DONE", "TESTING"):
        return "resolved"
    if status in ("FAILED", "CANCELED", "CANCELLED"):
        return "failed"
    if status in ("REVIEW", "IN_PROGRESS", "EXECUTING", "TODO"):
        return "in_flight"
    return "unknown"


def safe_action_for(matches) -> str:
    """Recommend an action given the queryset of historical matches.

    The queryset is expected to be ordered newest-first.  We look only
    at the most recent ``MAX_HISTORY`` entries to keep the signal local
    (ancient history is stale — yesterday's TLS error doesn't constrain
    today's model).

    The math:

    * If ≥ 3 matches and *success rate* ≥ 0.8 → ``"requeue"``
      (history says retry always works; backoff can be relaxed).
    * If ≥ 3 matches and *success rate* ≤ 0.2 → ``"escalate"``
      (history says we never self-heal — fall through to human).
    * Otherwise → ``"hold"`` (mixed signal, keep the stock policy).

    Below the MIN_HISTORY threshold we return ``"hold"`` — too few
    samples to recommend anything beyond the default policy.
    """
    rows = list(matches[:MAX_HISTORY]) if matches is not None else []
    rows = [r for r in rows if r is not None]
    if len(rows) < MIN_HISTORY:
        return ACTION_HOLD

    resolved = sum(1 for r in rows if _resolved_status(r) == "resolved")
    failed = sum(1 for r in rows if _resolved_status(r) == "failed")
    sample = resolved + failed
    if sample < MIN_HISTORY:
        return ACTION_HOLD
    success_rate = resolved / sample
    if success_rate >= SUCCESS_THRESHOLD:
        return ACTION_REQUEUE
    if success_rate <= ESCALATE_THRESHOLD:
        return ACTION_ESCALATE
    return ACTION_HOLD


# Tuned in tests; conservative defaults suit a small mistakes ledger.
MIN_HISTORY = 3
MAX_HISTORY = 10
SUCCESS_THRESHOLD = 0.8
ESCALATE_THRESHOLD = 0.2


def advice_for(fingerprint: str) -> Dict[str, Any]:
    """Build the triage advice blob for *fingerprint*.

    Returns a dict::

        {
          "fingerprint": "401 authentication_failed / claude / execution",
          "seen_count":  N,         # past entries with this fingerprint
          "safe_action": "requeue" | "hold" | "escalate",
          "last_resolution": "<one-liner>" | None,    # most recent DONE/TESTING
          "last_resolution_one_liner": str | None,    # alias
          "history": [
              {"task_id": int, "status": str, "one_liner": str, "created_at": iso},
              ...
          ],
        }

    A second occurrence of the same fingerprint surfaces the *last
    resolution* one-liner verbatim — the brief calls this out as the
    "second occurrence quotes the first" acceptance criterion.
    """
    matches = matches_query(fingerprint)
    rows = list(matches[:MAX_HISTORY])
    seen_count = matches.count() if hasattr(matches, "count") else len(rows)

    # Most recent successful (DONE / TESTING) entry — that is the
    # resolution we quote when the next failure arrives.  Falls back to
    # the most recent entry of any kind for the rare "all fail so far"
    # case so the comment always carries one named line.
    last_success = next(
        (r for r in rows if _resolved_status(r) == "resolved"), None,
    )
    last_any = rows[0] if rows else None
    quoted = last_success or last_any
    last_resolution = (
        getattr(quoted, "one_liner", None) if quoted is not None else None
    )

    history = []
    for r in rows:
        history.append({
            "task_id": r.task_id,
            "status": _resolved_status(r),
            "one_liner": r.one_liner,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })

    return {
        "fingerprint": fingerprint,
        "seen_count": seen_count,
        "safe_action": safe_action_for(rows),
        "last_resolution": last_resolution,
        "last_resolution_one_liner": last_resolution,
        "history": history,
    }


def format_advice_line(advice: Dict[str, Any]) -> str:
    """Render the brief-mandated triage line for the audit comment.

    The shape is::

        fingerprint_history: 401 authentication_failed / claude / execution
        seen 3 times before; last resolution: <one-liner>; safe action: requeue

    A first occurrence (no matches) returns a single "no prior matches"
    line so the comment schema is consistent.
    """
    fp = advice.get("fingerprint", "")
    seen = advice.get("seen_count", 0)
    if not seen:
        return (
            f"fingerprint_history: {fp}\n"
            f"seen 0 times before; safe action: hold (no history)"
        )
    last = advice.get("last_resolution") or "(no successful resolution on record)"
    action = advice.get("safe_action", ACTION_HOLD)
    return (
        f"fingerprint_history: {fp}\n"
        f"seen {seen} times before; last resolution: {last}; safe action: {action}"
    )


# ── Internal helpers used by tests / mistakes.py ──────────────────


def history_lines(matches, *, limit: int = MAX_HISTORY) -> Iterable[str]:
    """Iterate compact ``"task #N status one_liner"`` strings for the
    most recent *limit* matches.  Used by the audit comment when
    escalating so the operator can see *why* we're skipping the
    policy's requeue path.
    """
    for r in list(matches[:limit]):
        yield (
            f"  #{getattr(r, 'task_id', '?')} "
            f"({_resolved_status(r)}) {getattr(r, 'one_liner', '')}"
        )


def should_override_auto_requeue(advice: Dict[str, Any]) -> bool:
    """True if the fingerprint history says this failure needs a human.

    Used by :func:`tasks.failure_policy.apply_failure_policy` to skip
    the stock AUTO_REQUEUE retry path when the prior instances never
    self-healed.  Conservative: only escalate when the history is
    both large enough and overwhelmingly failed.
    """
    if not advice:
        return False
    return (
        advice.get("safe_action") == ACTION_ESCALATE
        and advice.get("seen_count", 0) >= MIN_HISTORY
    )


def should_skip_backoff(advice: Dict[str, Any]) -> bool:
    """True if the fingerprint history says retry always works.

    Used by :func:`tasks.failure_policy._auto_requeue` to skip the
    policy's ``backoff_seconds`` stamp on the metadata when the
    historical signal is overwhelmingly positive.
    """
    if not advice:
        return False
    return (
        advice.get("safe_action") == ACTION_REQUEUE
        and advice.get("seen_count", 0) >= MIN_HISTORY
    )


def advice_for_failure(
    *,
    failure_class: str,
    reason: str,
    agent: str = "",
    model: str = "",
    stage: str = STAGE_EXECUTION,
) -> Dict[str, Any]:
    """Convenience: compute the fingerprint, then look up advice.

    Single-call helper used by ``tasks.mistakes`` and the failure-policy
    audit comment.  Returns an empty ``advice`` shape when no fingerprint
    could be computed (empty failure_class).
    """
    fp = compute_fingerprint(
        failure_class=failure_class,
        reason=reason,
        agent=agent,
        model=model,
        stage=stage,
    )
    return advice_for(fp)


# ── Public re-exports used by callers ─────────────────────────────

__all__ = [
    "ACTION_ESCALATE",
    "ACTION_HOLD",
    "ACTION_REQUEUE",
    "MAX_HISTORY",
    "MIN_HISTORY",
    "STAGE_EXECUTION",
    "STAGE_REFLECTION",
    "STAGES",
    "advice_for",
    "advice_for_failure",
    "compute_fingerprint",
    "format_advice_line",
    "history_lines",
    "matches_query",
    "safe_action_for",
    "should_override_auto_requeue",
    "should_skip_backoff",
]
