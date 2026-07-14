#!/usr/bin/env python3
"""Emit GitHub Actions outputs for monorepo path filters."""

from __future__ import annotations

import fnmatch
import os
import subprocess


FILTERS = {
    "odin": [
        ".github/workflows/ci.yml",
        ".github/scripts/**",
        "odin/**",
    ],
    "taskit_backend": [
        ".github/workflows/ci.yml",
        ".github/scripts/**",
        "taskit/taskit-backend/**",
    ],
    "taskit_frontend": [
        ".github/workflows/ci.yml",
        ".github/scripts/**",
        "taskit/taskit-frontend/**",
    ],
    "snapshots": [
        ".github/workflows/ci.yml",
        ".github/scripts/**",
        "tests/e2e_snapshots/**",
        "odin/**",
        "taskit/taskit-backend/**",
        "taskit/taskit-frontend/**",
    ],
}


def _changed_files(base_sha: str, head_sha: str) -> list[str]:
    if not base_sha or set(base_sha) == {"0"}:
        output = subprocess.check_output(
            ["git", "ls-tree", "-r", "--name-only", head_sha],
            text=True,
        )
    else:
        output = subprocess.check_output(
            ["git", "diff", "--name-only", base_sha, head_sha],
            text=True,
        )
    return [line.strip() for line in output.splitlines() if line.strip()]


def _matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def main() -> None:
    base_sha = os.environ["BASE_SHA"]
    head_sha = os.environ["HEAD_SHA"]
    changed = _changed_files(base_sha, head_sha)

    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        for name, patterns in FILTERS.items():
            value = "true" if any(_matches(path, patterns) for path in changed) else "false"
            output.write(f"{name}={value}\n")


if __name__ == "__main__":
    main()
