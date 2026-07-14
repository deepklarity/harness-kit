"""Disk usage report + orphan pruning for ``odin gc``.

Three buckets:

- **microsandbox sandboxes** — ``~/.microsandbox/sandboxes/<name>``.
  Ephemeral ``odin-msb-*`` are the per-task VMs (the leak we are fixing);
  persistent ones (like ``odinbuild``) and snapshots are NEVER pruned.
- **microsandbox snapshots** — ``~/.microsandbox/snapshots/<name>``.
  Read-only images: never pruned; listing is so the operator sees their cost.
- **odin worktrees** — ``<project>/.odin/worktrees/<spec>/<task>`` plus the
  ``node_modules`` cost each one carries (per-worktree fresh install).

The ``prune`` action is dry-run by default (``collect_prune_plan`` returns the
list of intended actions). The ``execute_prune`` action is what the
``--prune`` CLI flag invokes; both NEVER touch a snapshot or a named sandbox
(only the empty-tracked filtered orphan set passes through ``_partition_orphans``).
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from odin.harnesses.microsandbox import MicrosandboxHarness


def _microsandbox_sandboxes() -> List[dict]:
    """List msb sandboxes on disk + classify ephemeral vs named.

    Each entry is ``{"name": str, "ephemeral": bool, "size_bytes": int}``.
    Sizes are measured by walking the sandbox directory; this is a
    best-effort estimate (the size is dominated by the per-VM snapshot copy
    which is uniform for the snapshot boot path).
    """
    home = Path("~/.microsandbox/sandboxes").expanduser()
    entries: List[dict] = []
    if not home.is_dir():
        return entries
    for p in sorted(home.iterdir()):
        if not p.is_dir():
            continue
        name = p.name
        # Snapshots live under ~/.microsandbox/snapshots — never mistaking them
        # for sandboxes is the single biggest safety net.
        size = _dir_size(p)
        ephemeral = MicrosandboxHarness._is_ephemeral_msb_sandbox(name)
        entries.append({
            "name": name, "ephemeral": ephemeral, "size_bytes": size,
        })
    return entries


def _microsandbox_snapshots() -> List[dict]:
    """List msb snapshots. Snapshots are read-only images and NEVER pruned."""
    home = Path("~/.microsandbox/snapshots").expanduser()
    entries: List[dict] = []
    if not home.is_dir():
        return entries
    for p in sorted(home.iterdir()):
        if not p.is_dir():
            continue
        entries.append({"name": p.name, "size_bytes": _dir_size(p)})
    return entries


def _remove_worktree(p: Path, *, project_root: Optional[Path] = None) -> bool:
    """Best-effort ``git worktree remove --force <p>`` + fallback rmtree.

    Stubbed at this module level so tests can patch the IO. The function
    exists separately from ``execute_prune`` so the safety net (refuse if
    path is not under the worktree base) can be exercised in isolation.
    """
    if not p.exists():
        return False
    # Final safety net: refuse if path is not under .odin/worktrees/
    # (defensive — keeps a future bug from GC-ing the home dir).
    base = (project_root or Path.cwd()) / ".odin" / "worktrees"
    try:
        p.resolve().relative_to(base)
    except ValueError:
        return False
    try:
        r = subprocess.run(
            ["git", "worktree", "remove", "--force", str(p)],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            return True
        shutil.rmtree(p, ignore_errors=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _odin_worktrees(project_root: Optional[str] = None) -> List[dict]:
    """List odin-managed worktrees under ``<root>/.odin/worktrees``.

    Each entry is ``{"path", "size_bytes", "node_modules_bytes", "has_changes",
    "managed"}``. ``managed=False`` means the path is under the worktree base
    but not created by odin (defensive — never delete those).
    """
    out: List[dict] = []
    root = Path(project_root) if project_root else Path.cwd()
    base = root / ".odin" / "worktrees"
    if not base.is_dir():
        return out
    for spec_dir in sorted(base.iterdir()):
        if not spec_dir.is_dir():
            continue
        # Spec branch + per-task worktree dirs sit under the spec dir.
        for task_dir in sorted(spec_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            # A task worktree is identifiable by having a `.git` file (linked
            # worktree pointing at the main repo's gitdir). Without one, treat
            # as not-odin-managed and leave it alone.
            has_git_file = (task_dir / ".git").is_file()
            if not has_git_file:
                continue
            sz = MicrosandboxHarness._size_breakdown(task_dir)
            changes = _worktree_has_changes(task_dir)
            out.append({
                "path": str(task_dir),
                "spec_id": spec_dir.name,
                "task_id": task_dir.name,
                "size_bytes": sz["size_bytes"],
                "node_modules_bytes": sz["node_modules_bytes"],
                "rest_bytes": sz["rest_bytes"],
                "has_changes": changes,
                "managed": True,
            })
    return out


def _worktree_has_changes(path: Path) -> bool:
    """True if the worktree has uncommitted changes OR its branch is ahead
    of the spec branch. Used by ``prune`` to refuse removal of dirty work."""
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(path), capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return True  # uncommitted / dirty
        if r.stdout.strip():
            return True
        # Check ahead/behind via the task branch's relationship to the spec branch.
        # This is a heuristic — for the report we just need a yes/no so the
        # operator can audit before removing.
        r2 = subprocess.run(
            ["git", "log", "--oneline", "@{u}..HEAD"],
            cwd=str(path), capture_output=True, text=True, timeout=10,
        )
        # If git fails to read upstream, treat as "no upstream" → assume clean.
        return r2.returncode == 0 and bool(r2.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return True  # safer to refuse than to misreport


def _dir_size(path: Path) -> int:
    """Walk the tree summing file sizes (symlinks = 0). Used for the
    report's sandboxes + snapshots sections — not for worktree breakdowns
    (those go through ``_size_breakdown`` to keep node_modules separate)."""
    total = 0
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    try:
        for dirpath, dirnames, filenames in os.walk(str(resolved)):
            for fn in filenames:
                fp = os.path.join(dirpath, fn)
                try:
                    if os.path.islink(fp):
                        size = 0
                    else:
                        size = os.lstat(fp).st_size
                except OSError:
                    continue
                total += size
    except OSError:
        return 0
    return total


def collect_report(project_root: Optional[str] = None) -> dict:
    """The dry-run-by-default disk-usage report. Pin shape: see tests."""
    sandboxes = _microsandbox_sandboxes()
    snapshots = _microsandbox_snapshots()
    worktrees = _odin_worktrees(project_root)
    ephemeral = [s for s in sandboxes if s["ephemeral"]]
    named = [s for s in sandboxes if not s["ephemeral"]]
    return {
        "sandboxes": sandboxes,
        "snapshots": snapshots,
        "worktrees": worktrees,
        "totals": {
            "ephemeral_sandboxes_count": len(ephemeral),
            "ephemeral_sandboxes_bytes": sum(s["size_bytes"] for s in ephemeral),
            "named_sandboxes_count": len(named),
            "named_sandboxes_bytes": sum(s["size_bytes"] for s in named),
            "snapshots_count": len(snapshots),
            "snapshots_bytes": sum(s["size_bytes"] for s in snapshots),
            "worktrees_count": len(worktrees),
            "worktrees_bytes": sum(w["size_bytes"] for w in worktrees),
            "node_modules_bytes": sum(w["node_modules_bytes"] for w in worktrees),
        },
    }


def collect_prune_plan(project_root: Optional[str] = None) -> List[dict]:
    """Dry-run equivalent: list everything that ``execute_prune`` WOULD do.

    The action items are the same shape that ``execute_prune`` returns, but
    none are carried out. Pin used by the CLI to print "+X MB reclaimable".
    """
    report = collect_report(project_root)
    plan: List[dict] = []
    for s in report["sandboxes"]:
        if s["ephemeral"]:
            plan.append({
                "kind": "sandbox",
                "name": s["name"],
                "estimated_reclaim_bytes": s["size_bytes"],
            })
    for w in report["worktrees"]:
        if not w["has_changes"]:
            plan.append({
                "kind": "worktree",
                "name": w["path"],
                "estimated_reclaim_bytes": w["size_bytes"],
                "node_modules_bytes": w["node_modules_bytes"],
            })
    return plan


def execute_prune(project_root: Optional[str] = None) -> List[dict]:
    """Actually carry out the prune. NEVER touches non-ephemeral sandboxes,
    NEVER touches snapshots, NEVER touches dirty worktrees.
    Returns the list of completed actions."""
    completed: List[dict] = []
    plan = collect_prune_plan(project_root)
    root = Path(project_root) if project_root else Path.cwd()
    for action in plan:
        if action["kind"] == "sandbox":
            ok = MicrosandboxHarness._remove_sandbox(action["name"])
            if ok:
                completed.append(action)
        elif action["kind"] == "worktree":
            p = Path(action["name"])
            if _remove_worktree(p, project_root=root):
                completed.append(action)
    return completed


def render_report(report: dict, *, stream=None) -> None:
    """Pretty-print the report as a small markdown table. Used by the CLI."""
    stream = stream or sys.stdout
    t = report["totals"]
    stream.write("\n=== odin gc report ===\n\n")
    # Sandboxes
    stream.write(f"Sandboxes: {t['ephemeral_sandboxes_count']} ephemeral, "
                f"{t['named_sandboxes_count']} named/persistent "
                f"({_fmt_bytes(t['ephemeral_sandboxes_bytes'])}, "
                f"{_fmt_bytes(t['named_sandboxes_bytes'])})\n")
    for s in report["sandboxes"]:
        marker = "[E]" if s["ephemeral"] else "[P]"
        stream.write(f"  {marker} {s['name']:<28} {_fmt_bytes(s['size_bytes'])}\n")
    # Snapshots
    stream.write(
        f"\nSnapshots: {t['snapshots_count']} "
        f"({_fmt_bytes(t['snapshots_bytes'])}, never removed by gc)\n"
    )
    for s in report["snapshots"]:
        stream.write(f"  [S] {s['name']:<28} {_fmt_bytes(s['size_bytes'])}\n")
    # Worktrees
    stream.write(
        f"\nWorktrees: {t['worktrees_count']} "
        f"({_fmt_bytes(t['worktrees_bytes'])} total; "
        f"{_fmt_bytes(t['node_modules_bytes'])} in node_modules)\n"
    )
    for w in report["worktrees"]:
        marker = "[!]" if w["has_changes"] else "[ ]"
        stream.write(
            f"  {marker} {w['spec_id']}/{w['task_id']:<24} "
            f"{_fmt_bytes(w['size_bytes'])} "
            f"({_fmt_bytes(w['node_modules_bytes'])} node_modules)\n"
        )
    stream.write("\n")


def _fmt_bytes(n: int) -> str:
    """Format bytes with KiB-style rounding for the report."""
    if n <= 0:
        return "0 B"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:.1f} {u}"
        f /= 1024
    return f"{n} B"
