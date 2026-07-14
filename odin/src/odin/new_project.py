"""`odin new-project` — bootstrap Odin end-to-end onto a new or existing repo.

Pure/disk helpers live here so they're testable without a TaskIt server;
the CLI command (`OdinCLI.new_project` in cli.py) wires them together with
the TaskIt backend to create a board + sample task.

Safety: this module never commits to a repo that already has git history —
`ensure_git_repo` is a no-op once `.git` exists. Everything Odin does after
that (tasks, worktrees) happens on spec/task branches per convention.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

SAMPLE_TASK_TITLE = "Say hello to Odin"

SAMPLE_SPEC_MARKDOWN = """# Sample Task: Say Hello

This is the first task for this Odin-managed project. It's intentionally
tiny so you can watch a task go from planned to merged in minutes.

## Tasks

1. **Add a HELLO_ODIN.md file** — create `HELLO_ODIN.md` at the repo root
   containing a one-line greeting, e.g. `# Hello from Odin`. Commit it on
   the task branch. This is the smallest possible change — proof that the
   plan -> assign -> exec -> merge loop works end to end on this repo.
"""

_URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


class NewProjectError(Exception):
    """Raised for any new-project precondition or clone failure.

    Callers (the CLI) catch this and print a clean, actionable message
    instead of a traceback.
    """


def is_remote_url(target: str) -> bool:
    """True if `target` looks like something git can clone (not a local path)."""
    return bool(_URL_SCHEME_RE.match(target)) or target.startswith("git@")


def repo_name_from_target(target: str) -> str:
    """Derive a directory name from a clone URL or local path."""
    name = target.rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name or "project"


def already_initialized(path: Path) -> bool:
    """True if `path` already has an Odin project config."""
    return (Path(path) / ".odin" / "config.yaml").exists()


def clone_repo(url: str, dest: Path) -> None:
    """Clone `url` into `dest`. Raises NewProjectError on failure."""
    try:
        subprocess.run(
            ["git", "clone", url, str(dest)],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise NewProjectError("git is not installed or not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise NewProjectError(f"git clone failed: {detail}") from exc


def resolve_project_path(target: str, dest: Optional[str], cwd: Path) -> Path:
    """Resolve `target` to an absolute project directory, cloning if it's a URL.

    Raises NewProjectError if a local target doesn't exist, isn't a
    directory, or a clone destination is already occupied.
    """
    if is_remote_url(target):
        dest_path = (
            Path(dest).expanduser().resolve()
            if dest
            else (cwd / repo_name_from_target(target)).resolve()
        )
        if dest_path.exists():
            raise NewProjectError(
                f"Destination already exists: {dest_path}\n"
                "Pick a different --dest, or remove it and re-run."
            )
        clone_repo(target, dest_path)
        return dest_path

    path = Path(target).expanduser().resolve()
    if not path.exists():
        raise NewProjectError(f"Path does not exist: {path}")
    if not path.is_dir():
        raise NewProjectError(f"Not a directory: {path}")
    return path


def ensure_git_repo(path: Path) -> bool:
    """Ensure `path` is a git repo. Returns True if a new repo was initialized.

    No-op when `.git` already exists — never touches an existing repo's
    history (safe-by-default: this is the only thing standing between
    new-project and committing on top of someone else's main branch).
    """
    if (Path(path) / ".git").exists():
        return False

    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)

    gitignore = Path(path) / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(
            "# Odin internals\n"
            ".odin/worktrees/\n"
            ".odin/locks/\n"
            ".odin/logs/\n"
            ".odin/costs/\n"
            ".env\n"
        )

    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            # Explicit identity: a fresh sandbox/CI box may have no global
            # user.email/user.name, and this initial commit must not depend
            # on that being configured.
            "-c", "user.email=odin@harness.kit",
            "-c", "user.name=Odin",
            "commit", "-m", "Initial commit (odin new-project)",
        ],
        cwd=path,
        check=True,
        capture_output=True,
    )
    return True
