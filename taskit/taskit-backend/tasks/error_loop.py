"""Error-loop detection in the run reconciler (task #262).

A run whose trace tail is dominated by repeated errors of the same
signature is *looping* — the agent CLI is retrying a failing provider
call forever, the trace keeps growing (so the W6.15 progress-liveness
check sees fresh *writes* and passes), but no actual *work* is
happening. This is the gap that left task 252 EXECUTING for an hour in a
minimax stream-error retry loop with quota exhausted; the human caught
it only via their quota dashboard.

The detector tails the run trace (the same ``task_<id>.trace.jsonl``
W6.15 stats for mtime), classifies each line as error / non-error using
the existing ``failure_tagger`` signature sets, and normalizes each
error to a W6.3 fingerprint signature. When a configurable share
(default >= 80%) of the tail shares one signature, the run is looping.

Pure logic — no Django model access, no side effects. The reconciler
(``dag_executor._reap_error_loop_runs``) owns the kill + requeue; this
module only answers "is this trace a loop?".
"""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from .failure_tagger import classify_failure_text
from .fingerprints import STAGE_EXECUTION, compute_fingerprint

# Default tunables (mirrored as settings in config/settings.py so an
# operator can override without code changes).
DEFAULT_THRESHOLD = 0.8      # min share of same-signature error lines
DEFAULT_WINDOW = 50          # tail lines to examine
DEFAULT_MIN_LINES = 10       # need this many lines before judging
DEFAULT_PROVIDER_BACKOFF = 600   # seconds to wait out a provider window

# Failure classes whose error loop is a provider stream/quota problem —
# these warrant a backoff delay (the provider is down; retrying
# immediately just re-loops). Other classes loop for non-provider
# reasons and retry without a long wait.
PROVIDER_LOOP_CLASSES = frozenset({"quota_exhaustion", "transport_error"})

# Lines shorter than this carry too little signal to classify reliably
# (JSON framing, partial writes). Skipping them keeps the ratio honest.
_MIN_LINE_LEN = 8


@dataclass(frozen=True)
class LoopVerdict:
    """The result of examining one run trace for an error loop.

    ``is_looping`` is True only when the dominant error signature
    accounts for >= ``threshold`` of the tail lines. The
    ``dominant_class`` is the ``failure_tagger`` class of that signature
    (e.g. ``quota_exhaustion``); ``dominant_signature`` is the full W6.3
    fingerprint (``"<salient> / <provider> / execution"``).
    """

    is_looping: bool
    error_ratio: float           # dominant_signature lines / total lines
    dominant_class: str          # failure class of the dominant signature
    dominant_signature: str      # normalized W6.3 fingerprint
    error_lines: int             # lines matching the dominant signature
    total_lines: int             # non-blank lines examined
    sample: str                  # one representative error line (truncated)


def _read_tail_lines(path: Path, window: int) -> List[str]:
    """Return the last ``window`` non-blank lines of *path*.

    Reads the file backwards in chunks so a multi-megabyte trace (the
    looping case) is never fully loaded. Blank / whitespace-only lines
    are skipped — they carry no classification signal and would dilute
    the ratio.
    """
    if not Path(path).exists():
        return []
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            block = 4096
            data = b""
            while size > 0 and data.count(b"\n") <= window:
                step = min(block, size)
                size -= step
                fh.seek(size)
                data = fh.read(step) + data
        text = data.decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[-window:] if window > 0 else lines


def _extract_error_text(obj: dict) -> str:
    """Pull a human-readable message out of a JSON error event.

    Stream-json error shapes vary by CLI (Claude ``{"error":{"message"}}``,
    OpenAI ``{"error":{"message","type"}}``, generic ``{"message"}``).
    Returns the best available text for classification; empty when no
    message field is present.
    """
    err = obj.get("error")
    if isinstance(err, dict):
        for key in ("message", "detail", "type"):
            val = err.get(key)
            if isinstance(val, str) and val.strip():
                return val
    if isinstance(err, str) and err.strip():
        return err
    msg = obj.get("message")
    if isinstance(msg, str) and msg.strip():
        return msg
    return ""


def classify_trace_line(text: str) -> Optional[str]:
    """Classify a single trace line.

    Returns the ``failure_tagger`` class (e.g. ``quota_exhaustion``)
    when the line is an error, or ``None`` when it is benign content.

    Two detection paths, cheapest first:

    1. **JSON error event** — a parsed JSON object whose ``type`` is
       ``error`` (or that carries an ``error`` key) is an error by
       construction. Its message text is classified for the signature.
    2. **Free-text keyword match** — ``classify_failure_text`` over the
       raw line. A normal content line (``content_block_delta`` with
       prose) returns ``unknown`` → not an error. A line whose text
       carries quota / transport / auth keywords returns its class.

    JSON error events whose message has no recognisable keyword default
    to ``transport_error`` — a stream-level error with no richer signal
    is a transport-class failure, which is also a provider-loop class
    (and so earns the backoff).
    """
    raw = (text or "").strip()
    if len(raw) < _MIN_LINE_LEN:
        return None
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        obj = None
    if isinstance(obj, dict):
        otype = (obj.get("type") or "").lower()
        if otype == "error" or isinstance(obj.get("error"), (dict, str)):
            msg = _extract_error_text(obj)
            cls = classify_failure_text(msg) if msg else "unknown"
            return cls if cls != "unknown" else "transport_error"
    cls = classify_failure_text(raw)
    return cls if cls != "unknown" else None


def _line_signature(
    text: str, failure_class: str, *, agent: str = "", model: str = "",
) -> str:
    """Compute the W6.3 fingerprint for one error line.

    Reuses ``fingerprints.compute_fingerprint`` so the line-level
    signature is the same shape as the task-level mistake fingerprint —
    a looping run detected now and a task that FAILED on the same error
    later produce the same ``"<salient> / <provider> / execution"``
    string, so the league/fingerprint history joins across both.
    """
    return compute_fingerprint(
        failure_class=failure_class,
        reason=text,
        agent=agent,
        model=model,
        stage=STAGE_EXECUTION,
    )


def detect_error_loop(
    trace_path,
    *,
    agent: str = "",
    model: str = "",
    threshold: float = DEFAULT_THRESHOLD,
    window: int = DEFAULT_WINDOW,
    min_lines: int = DEFAULT_MIN_LINES,
) -> Optional[LoopVerdict]:
    """Examine one trace file and decide whether the run is error-looping.

    Returns a :class:`LoopVerdict` (``is_looping`` True/False) when there
    is enough data to judge, or ``None`` when the trace is missing or
    too short to evaluate (``< min_lines`` non-blank lines).

    A run is looping when the **dominant** error signature (the single
    most-common W6.3 fingerprint among error lines) accounts for >=
    ``threshold`` of the tail lines. A busy-but-working trace (zero or
    scattered errors) never reaches the threshold and is left alone.
    """
    lines = _read_tail_lines(Path(trace_path), window)
    total = len(lines)
    if total < min_lines:
        return None

    sig_counts: Counter = Counter()
    sig_class: dict = {}
    sig_sample: dict = {}
    for line in lines:
        cls = classify_trace_line(line)
        if cls is None:
            continue
        sig = _line_signature(line, cls, agent=agent, model=model)
        sig_counts[sig] += 1
        sig_class.setdefault(sig, cls)
        sig_sample.setdefault(sig, line)

    if not sig_counts:
        return LoopVerdict(
            is_looping=False, error_ratio=0.0, dominant_class="",
            dominant_signature="", error_lines=0, total_lines=total, sample="",
        )

    dominant_sig, dominant_count = sig_counts.most_common(1)[0]
    ratio = dominant_count / total
    is_looping = ratio >= threshold
    sample = sig_sample.get(dominant_sig, "")
    return LoopVerdict(
        is_looping=is_looping,
        error_ratio=ratio,
        dominant_class=sig_class.get(dominant_sig, ""),
        dominant_signature=dominant_sig,
        error_lines=dominant_count,
        total_lines=total,
        sample=sample[:300],
    )


def is_provider_loop(verdict: Optional[LoopVerdict]) -> bool:
    """True when the loop's dominant class is a provider stream/quota class.

    Provider loops earn a backoff delay (the provider is down; an
    immediate retry just re-loops). Non-provider loops retry without the
    long wait.
    """
    return bool(verdict and verdict.dominant_class in PROVIDER_LOOP_CLASSES)


def provider_backoff_seconds(
    verdict: Optional[LoopVerdict],
    *,
    default: float = 0,
    provider_delay: float = DEFAULT_PROVIDER_BACKOFF,
) -> float:
    """Return the backoff delay (seconds) for an error-loop verdict.

    Provider stream/quota classes → ``provider_delay`` (default 10 min,
    "waiting out the window"). Everything else → ``default`` (0 — retry
    promptly; the loop was not a provider outage).
    """
    if is_provider_loop(verdict):
        return provider_delay
    return default
