#!/usr/bin/env python3
"""Write the monorepo CI test-count table to GitHub's job summary."""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


RESULTS_DIR = Path("ci-results")


@dataclass(frozen=True)
class Counts:
    total: int | None = None
    passed: int | None = None
    failed: int | None = None
    skipped: int | None = None


def _fmt(value: int | None) -> str:
    return "-" if value is None else str(value)


def _sum_junit(path: Path) -> Counts:
    if not path.exists():
        return Counts()

    root = ET.parse(path).getroot()
    suites = root.findall("testsuite") if root.tag == "testsuites" else [root]
    total = failed = skipped = 0
    for suite in suites:
        total += int(suite.attrib.get("tests", "0"))
        failures = int(suite.attrib.get("failures", "0"))
        errors = int(suite.attrib.get("errors", "0"))
        failed += failures + errors
        skipped += int(suite.attrib.get("skipped", "0"))

    return Counts(total=total, passed=total - failed - skipped, failed=failed, skipped=skipped)


def _sum_django_log(path: Path) -> Counts:
    if not path.exists():
        return Counts()

    text = path.read_text(errors="replace")
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    total_match = re.search(r"Ran (\d+) tests?", text)
    if not total_match:
        return Counts()

    total = int(total_match.group(1))
    failed = 0
    skipped = 0
    failed_summaries = re.findall(r"FAILED \(([^)]*(?:failures|errors)[^)]*)\)", text)
    if failed_summaries:
        for key, value in re.findall(r"(failures|errors|skipped)=(\d+)", failed_summaries[-1]):
            if key == "skipped":
                skipped += int(value)
            else:
                failed += int(value)
    ok_match = re.search(r"OK(?: \(([^)]+)\))?", text)
    if ok_match and ok_match.group(1):
        skipped_match = re.search(r"skipped=(\d+)", ok_match.group(1))
        if skipped_match:
            skipped = int(skipped_match.group(1))

    return Counts(total=total, passed=total - failed - skipped, failed=failed, skipped=skipped)


def _count_vitest_assertions(data: dict) -> Counts:
    if "numTotalTests" in data:
        total = int(data.get("numTotalTests", 0))
        passed = int(data.get("numPassedTests", 0))
        failed = int(data.get("numFailedTests", 0))
        skipped = int(data.get("numPendingTests", 0))
        return Counts(total=total, passed=passed, failed=failed, skipped=skipped)

    assertions = []
    for test_file in data.get("testResults", []):
        assertions.extend(test_file.get("assertionResults", []))
    if not assertions:
        return Counts()

    passed = sum(1 for item in assertions if item.get("status") == "passed")
    failed = sum(1 for item in assertions if item.get("status") == "failed")
    skipped = sum(1 for item in assertions if item.get("status") in {"pending", "skipped", "todo"})
    return Counts(total=len(assertions), passed=passed, failed=failed, skipped=skipped)


def _sum_vitest_json(path: Path) -> Counts:
    if not path.exists():
        return Counts()

    with path.open() as f:
        data = json.load(f)
    return _count_vitest_assertions(data)


def main() -> None:
    suites = [
        ("Odin pytest", os.environ.get("ODIN_RESULT", "unknown"), _sum_junit(RESULTS_DIR / "odin-pytest.xml")),
        ("TaskIt Django", os.environ.get("TASKIT_DJANGO_RESULT", "unknown"), _sum_django_log(RESULTS_DIR / "taskit-django.log")),
        ("TaskIt Vitest", os.environ.get("TASKIT_VITEST_RESULT", "unknown"), _sum_vitest_json(RESULTS_DIR / "taskit-vitest.json")),
        ("Snapshots", os.environ.get("SNAPSHOTS_RESULT", "unknown"), _sum_junit(RESULTS_DIR / "snapshots.xml")),
    ]

    lines = [
        "## Monorepo CI test counts",
        "",
        "| Suite | Result | Total | Passed | Failed | Skipped |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for suite, result, counts in suites:
        lines.append(
            f"| {suite} | {result} | {_fmt(counts.total)} | {_fmt(counts.passed)} | "
            f"{_fmt(counts.failed)} | {_fmt(counts.skipped)} |"
        )
    lines.append("")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write("\n".join(lines))
    else:
        print("\n".join(lines))


if __name__ == "__main__":
    main()
