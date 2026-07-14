"""Offline replay harness for warm-start floor tuning — task #242.

Replays a corpus of historical task briefs against the repo's actual docs tree
and prints the coverage curve (% of briefs with at least one match at each
floor) plus a relevance spot-check. Run once to pick :data:`MIN_SCORE` against
measured coverage rather than a guess.

    python3 tests/disk/run_warm_start_replay.py [briefs.json] [docs_root]

Defaults: ``warm_start_briefs.json`` beside this script, and the repo's real
``docs/`` (resolved from this file's location). The briefs file is mined from
git merge/auto-commit subjects (real dispatched titles) — see the proof for
provenance.

This is a measurement script, NOT a unit test (it reads the live repo docs).
``test_warm_start_replay.py`` unit-tests the :func:`coverage_curve` machinery
against fixtures.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "src"))

from odin import warm_start as w

FLOORS = [0.0, 0.01, 0.02, 0.03, 0.05, 0.08, 0.10, 0.12, 0.15, 0.20, 0.30]


def main():
    briefs_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "warm_start_briefs.json")
    docs_root = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "..", "..", "..", "docs")

    with open(briefs_path) as fh:
        briefs = json.load(fh)

    print(f"# warm-start replay — {len(briefs)} briefs vs {os.path.abspath(docs_root)}")
    corpus = w._build_corpus(docs_root)
    print(f"# corpus entries: {len(corpus)}")

    out = w.coverage_curve(briefs, docs_root, FLOORS)
    print("\n## coverage curve (floor -> matched / total -> coverage %)")
    print(f"{'floor':>6}  {'matched':>7}  {'total':>5}  {'cov%':>5}")
    for c in out["curve"]:
        print(f"{c['floor']:>6.2f}  {c['matched']:>7}  {c['total']:>5}  {c['coverage']*100:>5.1f}")

    # relevance spot-check at the tuned floor
    target = w.MIN_SCORE
    print(f"\n## relevance spot-check (10 samples at floor={target}, tuned MIN_SCORE)")
    sample = w.relevance_sample(out["results"], k=10, floor=target)
    print(f"# {len(sample)} matched briefs sampled (best first)")
    for s in sample:
        top = s["top"]
        print(f"  #{s['id']:>4}  score={top['score']:.3f}  -> {top['path']}")
        print(f"        brief: {s['title'][:84]}")
        print(f"        reason: {top['reason'][:84]}")


if __name__ == "__main__":
    main()
