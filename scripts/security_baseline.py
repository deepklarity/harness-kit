"""Security-audit baseline parser + delta computer.

The security-audit preset (see taskit/taskit-backend/data/task_presets.json)
stores a baseline snapshot of finding counts after each run. The next run
reads the prior baseline, compares it to the current report, and reports
the delta — this is the security effect of a wave, expressed as
+/-/zero per severity bucket.

This module is the single point of truth for "is the repo's security
posture better or worse than last time?". It is dependency-free (stdlib
only) so the same parser is reusable from the harness runner, the
preset's description (for instantiating a board task that needs the
baseline), and the tests under tests/test_security_baseline.py.

Schema (version 1):

    {
      "version": 1,
      "counts": {"P0": <int>, "P1": <int>, "P2": <int>, "P3": <int>},
      "findings": [                # optional: structured findings
        {"id": <str>, "severity": "P0"|"P1"|"P2"|"P3",
         "location": <file:line>, "summary": <str>}
      ],
      "source": <str>,             # e.g. "task-245" or "wave-7"
      "captured_at": <iso8601>     # optional; auto-filled by write_baseline
    }
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERSION = 1

# The four severity buckets the security audit tracks. P4 (cosmetic) is
# intentionally excluded from the baseline — it doesn't move the needle
# and reporting it as a count invites theater.
BUCKETS: tuple[str, ...] = ("P0", "P1", "P2", "P3")


ZERO_BASELINE: dict[str, Any] = {
    "version": VERSION,
    "counts": {b: 0 for b in BUCKETS},
    "source": "zero",
}


class BaselineParseError(ValueError):
    """Raised when a baseline file is present but malformed."""


def load_baseline(path: Path) -> dict[str, Any]:
    """Load a baseline snapshot from disk.

    A missing file is NOT an error — the first run has no prior baseline;
    return ``ZERO_BASELINE`` so the delta is simply the current counts.
    A present-but-malformed file IS an error; the operator should not
    silently start over.
    """
    if not path.exists():
        return dict(ZERO_BASELINE)
    try:
        with open(path) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise BaselineParseError(f"baseline {path} is malformed JSON: {e}") from e

    if not isinstance(data, dict):
        raise BaselineParseError(f"baseline {path} must be a JSON object")
    if data.get("version") != VERSION:
        raise BaselineParseError(
            f"baseline {path} version {data.get('version')!r} != {VERSION}"
        )
    counts = data.get("counts")
    if not isinstance(counts, dict):
        raise BaselineParseError(f"baseline {path} missing 'counts' object")

    normalized = {b: int(counts.get(b, 0)) for b in BUCKETS}
    out = {
        "version": VERSION,
        "counts": normalized,
        "source": str(data.get("source", "unknown")),
    }
    if "captured_at" in data:
        out["captured_at"] = data["captured_at"]
    if "findings" in data:
        out["findings"] = data["findings"]
    return out


def write_baseline(
    path: Path, snapshot: dict[str, Any], *, source: str | None = None
) -> None:
    """Write a baseline snapshot to disk.

    The on-disk shape mirrors the in-memory shape exactly, plus a
    ``captured_at`` timestamp and a forced ``source`` if provided. Idempotent
    — overwriting the existing baseline is the expected next-run behavior.
    """
    if "counts" not in snapshot:
        raise BaselineParseError("snapshot missing 'counts'")
    payload: dict[str, Any] = {
        "version": VERSION,
        "counts": {b: int(snapshot["counts"].get(b, 0)) for b in BUCKETS},
        "source": source if source is not None else snapshot.get("source", "unknown"),
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if "findings" in snapshot:
        payload["findings"] = snapshot["findings"]

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")


def compute_delta(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, int]:
    """Compute the per-bucket delta between a baseline and a current snapshot.

    Positive = regressed (more findings than before).
    Negative = improved (fewer findings than before).
    Zero = unchanged.

    Both arguments may be either a full snapshot (with ``counts``) or just
    a counts dict — the parser handles both shapes for ergonomics.
    """
    base_counts = baseline.get("counts", baseline) if isinstance(baseline, dict) else {}
    cur_counts = current.get("counts", current) if isinstance(current, dict) else {}

    return {
        b: int(cur_counts.get(b, 0)) - int(base_counts.get(b, 0))
        for b in BUCKETS
    }


# ─── CLI ────────────────────────────────────────────────────────────────────


def _extract_counts(obj: Any) -> dict[str, int]:
    """Pull a counts dict from either a snapshot file (JSON object) or a
    counts-only JSON file (top-level dict). Falls back to the ZERO_BASELINE
    if the file has neither — this keeps the CLI useful for both shapes
    without forcing the caller to know the difference."""
    if isinstance(obj, dict):
        if "counts" in obj and isinstance(obj["counts"], dict):
            return {b: int(obj["counts"].get(b, 0)) for b in BUCKETS}
        if all(k in BUCKETS for k in obj.keys()):
            return {b: int(obj.get(b, 0)) for b in BUCKETS}
    return dict(ZERO_BASELINE["counts"])


def _cli(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Compute the delta between a baseline file (counts snapshot) "
            "and a current snapshot (JSON or JSON-with-counts). "
            "Prints `severity baseline current delta` per P0..P3."
        )
    )
    parser.add_argument("baseline", type=Path, help="Path to baseline JSON file.")
    parser.add_argument("current", type=Path, help="Path to current snapshot JSON.")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the per-bucket table; only print the JSON.",
    )
    args = parser.parse_args(argv)

    base = load_baseline(args.baseline)
    try:
        cur_obj = json.loads(args.current.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: could not read current snapshot {args.current}: {e}", file=sys.stderr)
        return 2
    cur_counts = _extract_counts(cur_obj)
    cur = {"version": VERSION, "counts": cur_counts, "source": "current"}
    delta = compute_delta(base, cur)

    if not args.quiet:
        print(f"{'severity':<10} {'baseline':>10} {'current':>10} {'delta':>10}")
        print(f"{'-'*10} {'-'*10:>10} {'-'*10:>10} {'-'*10:>10}")
        for b in BUCKETS:
            sign = "+" if delta[b] > 0 else ""
            print(
                f"{b:<10} {base['counts'][b]:>10} {cur_counts[b]:>10} "
                f"{sign}{delta[b]:>9}"
            )

    print(json.dumps(delta, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_cli())