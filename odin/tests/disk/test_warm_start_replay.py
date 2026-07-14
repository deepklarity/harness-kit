"""Tests for the warm-start replay harness — task #242.

The replay sweeps the match floor across a corpus of historical briefs and
reports the coverage curve (% of briefs with at least one match at each
floor) plus a relevance sample. This file unit-tests the pure
:func:`coverage_curve` machinery against a fixture docs tree + fixture
briefs; the real replay run (against the repo's actual docs and the
historical briefs snapshot) lives in ``run_warm_start_replay.py``.

Tags:
- [disk] — reads a tmp_path docs tree; no network, no subprocesses
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from odin import warm_start as w


_INDEX_MD = """# Breadcrumb Analyses

## Flows

| Folder | What it traces |
|--------|---------------|
| `planning-flow/` | End-to-end planning: spec creation, orchestrator, task creation. |
| `trace-data-pipeline/` | Trace capture and cost/token computation. |
| `token-economy/` | Token profiling: finding vs doing vs checking. |

## Quick navigation

### Planning
- **Planning failed or created wrong tasks?** → `planning-flow/DEBUG.md`
### Traces
- **Token count shows 0?** → `trace-data-pipeline/DEBUG.md`
"""

_BC_PLANNING_FLOW = """# Planning Flow

Spec creation through orchestrator dispatch. Decomposes a spec into tasks
with suggestive agent assignments and dependency edges.
"""

_BC_TRACE_DEBUG = """# Trace Data Pipeline Debug

Harness JSONL ingestion, cost/token column mapping, TraceViewer format gaps.
"""

_PATTERN = """tags: verification, rca, proof-of-work

# Close the Loop Before Claiming

A success claim is only reportable after a falsifying check.
"""


@pytest.fixture
def docs_root(tmp_path):
    root = tmp_path / "docs"
    bc = root / "breadcrumb_analysis"
    (bc / "planning-flow").mkdir(parents=True)
    (bc / "trace-data-pipeline").mkdir(parents=True)
    (bc / "token-economy").mkdir(parents=True)
    (bc / "_INDEX.md").write_text(_INDEX_MD)
    (bc / "planning-flow" / "FLOW.md").write_text(_BC_PLANNING_FLOW)
    (bc / "trace-data-pipeline" / "DEBUG.md").write_text(_BC_TRACE_DEBUG)
    (root / "patterns").mkdir(parents=True)
    (root / "patterns" / "close-the-loop.md").write_text(_PATTERN)
    return root


_BRIEFS = [
    {"id": "1", "title": "Planning agent created wrong tasks",
     "description": "Spec decomposition produced no tasks after planning."},
    {"id": "2", "title": "Token count shows 0 in trace viewer",
     "description": "Cost and token data mismatch between backend and UI."},
    {"id": "3", "title": "Token profiling finding vs doing",
     "description": "Decompose tool-call inputs into find/do/check buckets."},
    {"id": "4", "title": "Verify the fix live before claiming done",
     "description": "Close the loop with a falsifying check."},
    {"id": "5", "title": "zzz qqq xyzzy flumph",
     "description": "gibberish that matches nothing"},
]


class TestCoverageCurve:
    def test_curve_is_monotonic_nonincreasing(self, docs_root):
        floors = [0.0, 0.02, 0.05, 0.1, 0.2, 0.5]
        out = w.coverage_curve(_BRIEFS, docs_root, floors)
        curve = out["curve"]
        coverages = [c["coverage"] for c in curve]
        # as floor rises, coverage must stay flat or drop — never rise
        for a, b in zip(coverages, coverages[1:]):
            assert b <= a + 1e-9, (a, b)

    def test_zero_floor_matches_everything_with_overlap(self, docs_root):
        """At floor 0 any brief sharing a token with any entry matches; the
        gibberish brief still matches nothing (no shared tokens)."""
        out = w.coverage_curve(_BRIEFS, docs_root, [0.0])
        zero = out["curve"][0]
        # 4 of 5 briefs share tokens with the corpus; the gibberish one does not
        assert zero["matched"] == 4
        assert zero["total"] == 5

    def test_high_floor_matches_subset_of_low(self, docs_root):
        out = w.coverage_curve(_BRIEFS, docs_root, [0.0, 0.5])
        low, high = out["curve"]
        assert high["matched"] <= low["matched"]

    def test_results_carry_top_match(self, docs_root):
        out = w.coverage_curve(_BRIEFS, docs_root, [0.0])
        results = out["results"]
        assert len(results) == len(_BRIEFS)
        by_id = {r["id"]: r for r in results}
        # the planning brief's top match must be a planning-flow doc
        top = by_id["1"]["top"]
        assert top is not None
        assert "planning-flow" in top["path"]
        # the gibberish brief has no top match
        assert by_id["5"]["top"] is None
        assert by_id["5"]["max_score"] == 0.0

    def test_relevance_sample_picks_k(self, docs_root):
        """relevance_sample returns up to k matched briefs with their top
        suggestion, for the 10-sample spot-check in proof."""
        out = w.coverage_curve(_BRIEFS, docs_root, [0.05])
        matched = [r for r in out["results"] if r["max_score"] >= 0.05]
        sample = w.relevance_sample(out["results"], k=10, floor=0.05)
        assert len(sample) == min(10, len(matched))
        for s in sample:
            assert s["top"] is not None
            assert s["top"]["reason"]

    def test_empty_briefs_curve(self, docs_root):
        out = w.coverage_curve([], docs_root, [0.05])
        assert out["curve"][0]["total"] == 0
        assert out["curve"][0]["coverage"] == 0
