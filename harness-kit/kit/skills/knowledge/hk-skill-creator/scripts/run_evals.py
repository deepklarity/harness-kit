#!/usr/bin/env python3
"""Run the assertions in evals.json against one eval iteration.

    run_evals.py <workspace> <iteration-N>

evals.json, in the workspace:

    {"evals": [
      {"id": 1, "prompt": "...", "expected": "one sentence of what a good output holds",
       "checks": [
         {"file_exists": "report.html"},
         {"contains": ["report.html", "Findings"]},
         {"not_contains": ["report.html", "TODO"]}
       ]}
    ]}

Paths in a check are relative to <iteration-N>/eval-<id>/with_skill/ and the
same under without_skill/. Every check runs against both directories. A check
that passes in both measures nothing, and the report says so. Exit 1 when any
with-skill check fails.
"""
import json
import os
import sys


def run_check(base, check):
    (kind, arg), = check.items()
    if kind == "file_exists":
        return os.path.exists(os.path.join(base, arg))
    path, needle = arg
    full = os.path.join(base, path)
    if not os.path.exists(full):
        return False
    text = open(full, encoding="utf-8", errors="replace").read()
    if kind == "contains":
        return needle in text
    if kind == "not_contains":
        return needle not in text
    sys.exit(f"unknown check kind {kind!r}")


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    workspace, iteration = sys.argv[1], sys.argv[2]
    evals = json.load(open(os.path.join(workspace, "evals.json")))["evals"]
    failed = 0
    for ev in evals:
        print(f"\neval {ev['id']}: {ev['prompt'][:70]}")
        for check in ev["checks"]:
            (kind, arg), = check.items()
            label = f"{kind} {arg}"
            results = {}
            for side in ("with_skill", "without_skill"):
                base = os.path.join(workspace, iteration, f"eval-{ev['id']}", side)
                results[side] = run_check(base, check)
            w, wo = results["with_skill"], results["without_skill"]
            mark = "pass" if w else "FAIL"
            note = "  (passes without the skill too: measures nothing)" if w and wo else ""
            print(f"  {mark}  with {'y' if w else 'n'}  without {'y' if wo else 'n'}  {label}{note}")
            failed += not w
    print(f"\n{failed} with-skill check(s) failed" if failed else "\nevery with-skill check passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
