#!/usr/bin/env python3
"""why.py — answer "why does this line exist?"

Walks the provenance chain for a line (or range, or whole file) in a git
repo:

    blame  →  the commit that last touched the line
    trailer  →  Task-Id / Spec-Id recorded on that commit
    task / spec  →  the human-readable subject + identifiers

Every commit odin creates on the merge path (auto-commit + merge commit)
carries ``Task-Id:`` and ``Spec-Id:`` trailers (see
``odin.worktree._provenance_trailers``).  This tool makes those trailers
queryable from any line, so "why does this line exist" is a one-command
query instead of an archaeology session.

Usage
-----
    testing_tools/why.py <file>[:<line>]
    testing_tools/why.py path/to/module.py:42
    testing_tools/why.py path/to/module.py:42-60   # range
    testing_tools/why.py path/to/module.py:42,60    # two specific lines
    testing_tools/why.py path/to/module.py          # whole-file summary

Runs from anywhere inside a git worktree.  No external dependencies
(stdlib + ``git`` only) so it works without the venv or Django.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


# ---------------------------------------------------------------------
# git helpers
# ---------------------------------------------------------------------

def _git(args: List[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _repo_root(start: Path) -> Optional[Path]:
    """Return the worktree root for ``start``, or ``None`` if not in a repo."""
    r = _git(["rev-parse", "--show-toplevel"], cwd=start)
    if r.returncode != 0:
        return None
    return Path(r.stdout.strip())


@dataclass
class CommitInfo:
    """Provenance of a single commit, with parsed trailers."""
    sha: str
    subject: str = ""
    author: str = ""
    date: str = ""
    trailers: Dict[str, str] = field(default_factory=dict)

    @property
    def short(self) -> str:
        return self.sha[:10] if self.sha else "?"

    @property
    def task_id(self) -> Optional[str]:
        return self.trailers.get("Task-Id")

    @property
    def spec_id(self) -> Optional[str]:
        return self.trailers.get("Spec-Id")


def load_commit(root: Path, sha: str) -> CommitInfo:
    """Read subject/author/date/trailers for ``sha`` in one git call."""
    fmt = "%H%n%an <%ae>%n%ad%n%s%n%(trailers)"
    r = _git(["log", "-1", f"--format={fmt}", "--date=short", sha], cwd=root)
    info = CommitInfo(sha=sha)
    if r.returncode != 0 or not r.stdout.strip():
        return info
    parts = r.stdout.split("\n")
    if len(parts) >= 4:
        info.sha = parts[0].strip()
        info.author = parts[1].strip()
        info.date = parts[2].strip()
        info.subject = parts[3].strip()
    # Everything after the subject (parts[4:]) is the trailer block.
    for line in parts[4:]:
        line = line.strip()
        if ":" in line:
            key, _, val = line.partition(":")
            info.trailers[key.strip()] = val.strip()
    return info


def blame_lines(
    root: Path, rel_file: str, start: int, end: int
) -> Dict[int, str]:
    """Return ``{line_number: commit_sha}`` for lines ``start..end`` (inclusive).

    Uses ``git blame --porcelain`` so we get the SHA even for boundary /
    uncommitted-edge cases without depending on line-content parsing.
    """
    r = _git(
        ["blame", "-L", f"{start},{end}", "--porcelain", rel_file],
        cwd=root,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "git blame failed")
    result: Dict[int, str] = {}
    cur_sha: Optional[str] = None
    for line in r.stdout.split("\n"):
        if not line or line.startswith("\t"):
            continue
        if not line.startswith(" "):
            # Header line: "<sha> <orig-line> <final-line> [<num>]"
            tokens = line.split()
            if tokens:
                cur_sha = tokens[0]
        elif line.startswith("filename ") and cur_sha:
            pass
        # The header carries the final line number in field index 2.
        if cur_sha and not line.startswith(" ") and len(line.split()) >= 3:
            try:
                final_line = int(line.split()[2])
                result[final_line] = cur_sha
            except (ValueError, IndexError):
                pass
    return result


# ---------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------

@dataclass
class LineSpec:
    file: str
    lines: Optional[List[int]] = None  # None = whole file
    range_label: str = ""               # for display


def parse_target(arg: str) -> LineSpec:
    """Parse ``file``, ``file:N``, ``file:N-M``, ``file:N,M``."""
    if ":" not in arg:
        return LineSpec(file=arg)

    path_part, _, line_part = arg.rpartition(":")
    line_part = line_part.strip()
    if not line_part:
        return LineSpec(file=path_part)

    spec = LineSpec(file=path_part)
    if "-" in line_part and "," not in line_part:
        lo, _, hi = line_part.partition("-")
        lo_i, hi_i = int(lo), int(hi)
        spec.lines = list(range(lo_i, hi_i + 1))
        spec.range_label = f"{lo_i}-{hi_i}"
    elif "," in line_part:
        nums = [int(x) for x in line_part.split(",")]
        spec.lines = nums
        spec.range_label = line_part
    else:
        spec.lines = [int(line_part)]
        spec.range_label = line_part
    return spec


# ---------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------

def render_commit(info: CommitInfo) -> List[str]:
    """Render one commit's provenance block."""
    lines = [
        f"  commit   {info.short}",
        f"  subject  {info.subject or '(no subject)'}",
    ]
    if info.author:
        lines.append(f"  author   {info.author}")
    if info.date:
        lines.append(f"  date     {info.date}")
    task = info.task_id if info.task_id else "—"
    spec = info.spec_id if info.spec_id else "—"
    lines.append(f"  Task-Id  {task}")
    lines.append(f"  Spec-Id  {spec}")
    # Any non-provenance trailers are surfaced too (rare but honest).
    extra = {k: v for k, v in info.trailers.items()
             if k not in ("Task-Id", "Spec-Id")}
    for k, v in extra.items():
        lines.append(f"  {k:<8} {v}")
    return lines


def why_line(root: Path, rel_file: str, line_no: int) -> int:
    """Print provenance for a single line. Returns process exit code."""
    try:
        blamed = blame_lines(root, rel_file, line_no, line_no)
    except RuntimeError as exc:
        print(f"{rel_file}:{line_no}: {exc}", file=sys.stderr)
        return 1
    sha = blamed.get(line_no)
    if not sha:
        print(f"{rel_file}:{line_no}: no blame result", file=sys.stderr)
        return 1
    info = load_commit(root, sha)
    print(f"{rel_file}:{line_no}")
    for ln in render_commit(info):
        print(ln)
    return 0


def why_lines(root: Path, rel_file: str, line_nums: List[int]) -> int:
    """Print provenance for multiple specific lines, grouping repeats."""
    lo, hi = min(line_nums), max(line_nums)
    try:
        blamed = blame_lines(root, rel_file, lo, hi)
    except RuntimeError as exc:
        print(f"{rel_file}: {exc}", file=sys.stderr)
        return 1
    exit_code = 0
    seen: Dict[str, CommitInfo] = {}
    for ln in line_nums:
        sha = blamed.get(ln)
        if not sha:
            print(f"{rel_file}:{ln}: no blame result", file=sys.stderr)
            exit_code = 1
            continue
        if sha not in seen:
            seen[sha] = load_commit(root, sha)
        info = seen[sha]
        print(f"{rel_file}:{ln}")
        for row in render_commit(info):
            print(row)
        print()
    return exit_code


def why_file(root: Path, rel_file: str) -> int:
    """Whole-file summary: distinct commits + their trailers, line counts."""
    # Whole-file summary: distinct commits + their trailers, line counts.
    try:
        total = sum(1 for _ in open(root / rel_file, errors="replace"))
    except OSError:
        print(f"{rel_file}: empty or unreadable", file=sys.stderr)
        return 1
    if total == 0:
        print(f"{rel_file}: empty file", file=sys.stderr)
        return 1
    try:
        blamed = blame_lines(root, rel_file, 1, total)
    except RuntimeError as exc:
        print(f"{rel_file}: {exc}", file=sys.stderr)
        return 1
    # Group by commit.
    by_commit: Dict[str, List[int]] = {}
    for ln, sha in blamed.items():
        by_commit.setdefault(sha, []).append(ln)
    # Order by first appearance.
    ordered = sorted(by_commit.items(), key=lambda kv: min(kv[1]))
    print(f"{rel_file}  ({len(ordered)} commit{'s' if len(ordered) != 1 else ''}, "
          f"{total} line{'s' if total != 1 else ''})")
    print()
    for sha, lns in ordered:
        info = load_commit(root, sha)
        span = f"{min(lns)}-{max(lns)}" if min(lns) != max(lns) else str(min(lns))
        print(f"  lines {span:<10} ({len(lns)} line{'s' if len(lns) != 1 else ''})")
        for row in render_commit(info):
            print(row)
        print()
    return 0


# ---------------------------------------------------------------------
# main
# ---------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="why.py",
        description="Why does this line exist? Walks blame → trailer → task/spec.",
        usage="why.py <file>[:<line|range|list>]",
    )
    parser.add_argument(
        "target",
        help="file, file:LINE, file:START-END, or file:N,M",
    )
    args = parser.parse_args(argv)

    cwd = Path.cwd()
    root = _repo_root(cwd)
    if root is None:
        print("why.py: not inside a git repository", file=sys.stderr)
        return 2

    try:
        spec = parse_target(args.target)
    except ValueError:
        print(f"why.py: bad target {args.target!r}", file=sys.stderr)
        return 2

    rel = spec.file
    abs_path = (cwd / rel) if not Path(rel).is_absolute() else Path(rel)
    try:
        rel_file = abs_path.resolve().relative_to(root).as_posix()
    except ValueError:
        # File may be given relative to cwd already; try as-is.
        rel_file = rel

    # Existence check.
    if not (root / rel_file).exists():
        print(f"why.py: {rel_file}: no such file", file=sys.stderr)
        return 1

    if spec.lines is None:
        return why_file(root, rel_file)
    if len(spec.lines) == 1:
        return why_line(root, rel_file, spec.lines[0])
    return why_lines(root, rel_file, spec.lines)


if __name__ == "__main__":
    sys.exit(main())
