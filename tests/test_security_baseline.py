"""Tests for scripts/security_baseline.py — the security-audit baseline
parser + delta computer (task 245).

The parser reads a baseline JSON (count snapshot) and a current report,
and returns a delta = current - baseline for each severity bucket. The
parser is the single point of truth for "is this wave better or worse
than the last one?" — the user explicitly asked the next run to report
the delta, so the parser must be small, testable, and dependency-free.

Scenario matrix:
  Loading:
    - baseline file missing => returns zeroed baseline + note (not fatal)
    - baseline file present => loads and validates schema
    - baseline malformed JSON => raises BaselineParseError
    - baseline missing required fields => raises BaselineParseError
  Delta computation:
    - current == baseline => delta = {P0:0, P1:0, P2:0, P3:0}
    - current > baseline  => delta > 0 (security regressed)
    - current < baseline  => delta < 0 (security improved)
    - severity buckets not in baseline => default to 0
  Round-trip:
    - write_baseline() output is re-readable by load_baseline()
    - delta on identical snapshot is zero
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

# scripts/ isn't a package; import the parser directly. The parser is
# intentionally dependency-free (stdlib only) so this stays simple.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from security_baseline import (  # noqa: E402
    BaselineParseError,
    ZERO_BASELINE,
    compute_delta,
    load_baseline,
    write_baseline,
)


def _tmp_json(payload: dict) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(payload, f)
    f.close()
    return f.name


def _baseline(p0=0, p1=0, p2=0, p3=0, **extra) -> dict:
    payload = {
        "version": 1,
        "counts": {"P0": p0, "P1": p1, "P2": p2, "P3": p3},
        "source": "test",
    }
    payload.update(extra)
    return payload


def _current(p0=0, p1=0, p2=0, p3=0, **extra) -> dict:
    payload = {
        "version": 1,
        "counts": {"P0": p0, "P1": p1, "P2": p2, "P3": p3},
        "findings": [],
        "source": "test",
    }
    payload.update(extra)
    return payload


class TestLoadBaseline(unittest.TestCase):
    def test_missing_file_returns_zeroed(self):
        # First run — no prior baseline. We don't want this to be fatal; the
        # runner must still produce a report and write the first baseline.
        result = load_baseline(Path("/nonexistent/baseline-245-1234.json"))
        self.assertEqual(result["counts"], ZERO_BASELINE["counts"])

    def test_valid_baseline_loads(self):
        path = _tmp_json(_baseline(p0=1, p2=3))
        result = load_baseline(Path(path))
        self.assertEqual(result["counts"], {"P0": 1, "P1": 0, "P2": 3, "P3": 0})

    def test_malformed_json_rejected(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        f.write("{not json")
        f.close()
        with self.assertRaises(BaselineParseError):
            load_baseline(Path(f.name))

    def test_missing_counts_rejected(self):
        path = _tmp_json({"version": 1, "source": "x"})
        with self.assertRaises(BaselineParseError):
            load_baseline(Path(path))

    def test_missing_severity_buckets_default_to_zero(self):
        path = _tmp_json({"version": 1, "counts": {"P0": 2}, "source": "x"})
        result = load_baseline(Path(path))
        self.assertEqual(result["counts"]["P0"], 2)
        self.assertEqual(result["counts"]["P1"], 0)
        self.assertEqual(result["counts"]["P2"], 0)
        self.assertEqual(result["counts"]["P3"], 0)


class TestComputeDelta(unittest.TestCase):
    def test_identical_snapshots_zero_delta(self):
        baseline = _baseline(p0=1, p1=2, p2=3, p3=4)
        current = _current(p0=1, p1=2, p2=3, p3=4)
        delta = compute_delta(baseline, current)
        self.assertEqual(delta, {"P0": 0, "P1": 0, "P2": 0, "P3": 0})

    def test_regression_positive_delta(self):
        baseline = _baseline(p2=1)
        current = _current(p2=4)
        delta = compute_delta(baseline, current)
        self.assertEqual(delta["P2"], 3)
        # Other buckets stay zero.
        self.assertEqual(delta["P0"], 0)

    def test_improvement_negative_delta(self):
        baseline = _baseline(p1=5, p2=2)
        current = _current(p1=2, p2=2)
        delta = compute_delta(baseline, current)
        self.assertEqual(delta["P1"], -3)
        self.assertEqual(delta["P2"], 0)

    def test_first_run_zero_delta_against_zero_baseline(self):
        # The ZERO_BASELINE is what load_baseline returns when the file is
        # missing. First run delta must be exactly the current counts.
        baseline = ZERO_BASELINE
        current = _current(p0=0, p1=1, p2=2, p3=0)
        delta = compute_delta(baseline, current)
        self.assertEqual(delta, {"P0": 0, "P1": 1, "P2": 2, "P3": 0})


class TestBaselineRoundTrip(unittest.TestCase):
    def test_write_then_read_round_trip(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        f.close()
        path = Path(f.name)
        snapshot = _baseline(p0=2, p1=1, p2=7, p3=3, source="task-245")
        write_baseline(path, snapshot)
        loaded = load_baseline(path)
        self.assertEqual(loaded["counts"], snapshot["counts"])
        self.assertEqual(loaded["source"], "task-245")

    def test_round_trip_delta_is_zero(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        f.close()
        path = Path(f.name)
        snapshot = _baseline(p0=2, p1=1, p2=7, p3=3)
        write_baseline(path, snapshot)
        # Re-load and compare against the same snapshot — delta must be zero.
        loaded = load_baseline(path)
        delta = compute_delta(loaded, snapshot)
        self.assertEqual(delta, {"P0": 0, "P1": 0, "P2": 0, "P3": 0})


if __name__ == "__main__":
    unittest.main()