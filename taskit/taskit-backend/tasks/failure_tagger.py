"""Deterministic failure-class tagger.

Invoked at every task-FAILED transition point to distill the scattered
failure evidence (exit codes, trace signatures, timeout markers, quota
markers) into a single queryable label at ``metadata.failure_class``.

Design rules (v1):
- NO LLM classification. Pure deterministic pattern matching.
- Anything unmatched → ``unknown``. Never guess.
- Extends the existing quota-check discipline (``QUOTA_KEYWORDS``) —
  ``views._is_quota_failure`` imports this list so the two stay in lockstep.

Taxonomy (derived from the historical failure inventory in
``docs/fable_roadmap/archive/FINDINGS.md``):

    quota_exhaustion   — rate-limit / 429 / quota hit
    silent_hang        — zero output, terminated silently
    truncation         — truncated mid-generation, no ODIN-STATUS envelope
    env_missing        — API key unset, CLI not found, auth 401
    timeout            — execution exceeded its deadline
    stale_execution    — worker process died / never recorded
    worktree_isolation — worktree creation failed
    sandbox_unavailable — sandbox/runtime never started an agent (no agent ran)
    lock_race          — SQLite "database is locked"
    transport_error    — connection reset / RemoteProtocolError
    disk_exhaustion    — ENOSPC / no space left on device
    crash              — subprocess crash, unhandled exception
    cancelled          — user-initiated stop
    model_unavailable  — model not supported / invalid request
    unknown            — fallback (never guess)
"""

from __future__ import annotations

from typing import Any, Dict, Sequence

# ── Shared quota keywords ─────────────────────────────────────────────
# Single source of truth — ``views._is_quota_failure`` imports this list.
QUOTA_KEYWORDS = [
    "quota", "rate limit", "rate_limit", "429", "too many requests",
    "usage limit", "out of quota", "quota exceeded", "quota_failure",
]

# ── Taxonomy ──────────────────────────────────────────────────────────
FAILURE_CLASSES = frozenset({
    "quota_exhaustion",
    "silent_hang",
    "truncation",
    "env_missing",
    "timeout",
    "stale_execution",
    "worktree_isolation",
    "sandbox_unavailable",
    "lock_race",
    "transport_error",
    "disk_exhaustion",
    "crash",
    "cancelled",
    "model_unavailable",
    "error_loop",
    "unknown",
})

# ── Signature sets ────────────────────────────────────────────────────

_ENV_MISSING_SIGNATURES: Sequence[str] = (
    "api_key", "api key", "zai_api_key", "openai_api_key", "anthropic_api_key",
    "unauthorized", "401", "authentication error", "auth error",
    "ineligibletier", "ineligible tier", "not authed", "unauthed",
    "not found on path", "command not found",
    "odin_admin_user", "odin_admin_password",
    "key not found", "credential", "login required", "sign in",
    "device-code", "device code", "oauth",
)

_LOCK_RACE_SIGNATURES: Sequence[str] = (
    "database is locked", "database locked",
)

_DISK_SIGNATURES: Sequence[str] = (
    "no space left on device", "enospc", "disk full", "disk exhausted",
)

_TRANSPORT_SIGNATURES: Sequence[str] = (
    "remoteprotocolerror", "connection reset", "connection refused",
    "connection aborted", "connection broken",
    "remoteendclosedconnection", "chunkedencodingerror",
    "backend unreachable",
)

_SILENT_HANG_SIGNATURES: Sequence[str] = (
    "produced no output", "terminated silently",
    "zero output", "no output", "empty output",
    "hung", "did not produce",
)

_TRUNCATION_SIGNATURES: Sequence[str] = (
    "truncated mid-generation", "response truncated", "output truncated",
    "did not emit an odin-status", "did not emit odin-status",
    "output cap", "max tokens reached",
)

_TIMEOUT_SIGNATURES: Sequence[str] = (
    "timed out", "timeout", "deadline exceeded",
)

# failure_type values that indicate env/auth issues
_ENV_FAILURE_TYPES = frozenset({"backend_auth_failure", "cli_not_found"})

# failure_type values that indicate a crash/exception
_CRASH_FAILURE_TYPES = frozenset({
    "agent_execution_failure", "internal_error", "backend_exception",
})


def _has_any(text: str, signatures: Sequence[str]) -> bool:
    """Return True if any signature appears in *text*."""
    return any(sig in text for sig in signatures)


def classify_failure_text(text: str) -> str:
    """Classify a free-text blob into a taxonomy class.

    Same signature sets as :func:`classify_failure`, but for sources that
    don't carry a structured ``last_failure_type`` / ``last_failure_reason``
    pair (e.g. a reflection verdict summary). Quota is keyword-driven here
    since the structured ``ftype == "llm_call_failure"`` signal isn't
    available for reflections — the keyword set is the shared source of
    truth (``QUOTA_KEYWORDS``).
    """
    text = (text or "").lower()
    if not text.strip():
        return "unknown"
    if _has_any(text, QUOTA_KEYWORDS):
        return "quota_exhaustion"
    if _has_any(text, _ENV_MISSING_SIGNATURES):
        return "env_missing"
    if _has_any(text, _LOCK_RACE_SIGNATURES):
        return "lock_race"
    if _has_any(text, _DISK_SIGNATURES):
        return "disk_exhaustion"
    if _has_any(text, _TRANSPORT_SIGNATURES):
        return "transport_error"
    if _has_any(text, _TRUNCATION_SIGNATURES):
        return "truncation"
    if _has_any(text, _SILENT_HANG_SIGNATURES):
        return "silent_hang"
    if _has_any(text, _TIMEOUT_SIGNATURES):
        return "timeout"
    return "unknown"


def classify_failure(metadata: Dict[str, Any]) -> str:
    """Deterministically classify a task failure into a taxonomy class.

    Reads ``last_failure_type`` and ``last_failure_reason`` from *metadata*.
    Returns one of :data:`FAILURE_CLASSES`.

    Never guesses: anything unmatched returns ``"unknown"``.
    """
    ftype = (metadata.get("last_failure_type") or "").strip().lower()
    freason = (metadata.get("last_failure_reason") or "").strip().lower()
    text = f"{ftype} {freason}"

    # 1. Explicit user cancellation — unambiguous.
    if ftype == "cancelled":
        return "cancelled"

    # 2. Worktree isolation failure — unambiguous type.
    if ftype == "missing_worktree":
        return "worktree_isolation"

    # 2b. Sandbox/runtime never started an agent (task #331). No agent
    # ran, so this is a pre-execution infra failure — never a crash or a
    # silent agent. Unambiguous type from the orchestrator's evidence
    # ladder / pre-execution classifier.
    if ftype == "sandbox_unavailable":
        return "sandbox_unavailable"

    # 2c. Truncation — the evidence ladder (task #332) explicitly names an
    # output-cap truncation. Honor the explicit type so the truncation
    # AUTO_REQUEUE policy (which drives resume-in-place) fires even if the
    # reason text signatures shift.
    if ftype == "truncation":
        return "truncation"

    # 3. Stale execution — zombie process / no runner recorded.
    if ftype == "stale_execution":
        return "stale_execution"

    # 3b. Error loop — the reconciler's trace-tail detector (task #262)
    # already classified the dominant error signature; the reason text
    # carries it (e.g. "quota exhausted"). Re-derive from the reason so
    # a loop and a one-shot failure on the same provider share a class —
    # but fall back to the dedicated error_loop class when the reason
    # carries no keyword (a generic stream-error loop).
    if ftype == "error_loop":
        cls = classify_failure_text(freason)
        return cls if cls != "unknown" else "error_loop"

    # 4. Quota / rate-limit exhaustion (extends _is_quota_failure discipline).
    if ftype == "llm_call_failure" and _has_any(freason, QUOTA_KEYWORDS):
        return "quota_exhaustion"

    # 5. Environment / authentication missing — API keys, CLI, 401.
    if ftype in _ENV_FAILURE_TYPES or _has_any(text, _ENV_MISSING_SIGNATURES):
        return "env_missing"

    # 6. Lock race — SQLite "database is locked".
    if _has_any(text, _LOCK_RACE_SIGNATURES):
        return "lock_race"

    # 7. Disk exhaustion — ENOSPC.
    if _has_any(text, _DISK_SIGNATURES):
        return "disk_exhaustion"

    # 8. Transport error — connection drops / protocol errors.
    if _has_any(text, _TRANSPORT_SIGNATURES):
        return "transport_error"

    # 9. Truncation — output cut mid-generation / no ODIN-STATUS.
    #    Checked before silent_hang because the canonical "did not emit
    #    ODIN-STATUS" message mentions both "truncated" and "terminated
    #    silently" — the primary signal is truncation (there WAS output,
    #    it just got cut off).
    if _has_any(text, _TRUNCATION_SIGNATURES):
        return "truncation"

    # 10. Silent hang — zero output / terminated silently (no truncation sig).
    if _has_any(text, _SILENT_HANG_SIGNATURES):
        return "silent_hang"

    # 11. Timeout — execution exceeded its deadline.
    if ftype == "timeout" or _has_any(text, _TIMEOUT_SIGNATURES):
        return "timeout"

    # 12. Model unavailable — unsupported model / invalid request.
    if ftype == "model_escalation_failure":
        return "model_unavailable"

    # 13. Crash — subprocess exit, unhandled exception.
    if ftype in _CRASH_FAILURE_TYPES:
        return "crash"

    # 14. Unknown — never guess.
    return "unknown"


def tag_failure_class(metadata: Dict[str, Any]) -> str:
    """Classify and stamp ``failure_class`` into *metadata* in-place.

    Returns the class string for convenience.
    """
    cls = classify_failure(metadata)
    metadata["failure_class"] = cls
    return cls
