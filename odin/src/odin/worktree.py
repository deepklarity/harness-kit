"""Git worktree isolation for parallel task execution.

Each spec gets a branch (spec/<spec_id>) forked from main.
Each task gets a worktree with its own branch (task/<spec_id>/<task_id>)
forked from the spec branch. On task completion, the task branch is
merged back into the spec branch. On spec completion, a PR is created.

All git operations are serialized per-spec via file locks to prevent
concurrent merge races.
"""

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("odin.worktree")

AGENT_CONFIG_PATHS = (
    ".claude",
    ".codex",
    ".gemini",
    ".qwen",
    ".kilocode",
    ".odin/config.yaml",
    ".env",
    "opencode.json",
)

# Broader patterns for the worktree .gitignore — covers runtime artifacts
# (logs, costs, locks, specs) that odin generates inside the worktree.
_WORKTREE_GITIGNORE_PATHS = (
    ".claude",
    ".codex",
    ".gemini",
    ".qwen",
    ".kilocode",
    ".odin",
    ".env",
    "opencode.json",
)

_GITIGNORE_MARKER = "# odin-worktree-agent-configs"


@dataclass
class MergeResult:
    success: bool
    conflict: bool = False
    noop: bool = False
    error: Optional[str] = None
    diff_stat: Optional[str] = None


class WorktreeManager:
    """Manages git worktrees for spec/task isolation.

    Single owner of all git commands related to worktree lifecycle.
    All operations are idempotent — safe to retry on failure.
    """

    def __init__(
        self,
        project_root: Path,
        worktree_dir: str = ".odin/worktrees",
    ):
        self.project_root = Path(project_root).resolve()
        if not (self.project_root / ".git").exists():
            raise ValueError(f"Not a git repository: {self.project_root}")
        self.worktree_base = self.project_root / worktree_dir
        self.lock_dir = self.project_root / ".odin" / "locks"

    def _git(self, *args: str, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
        """Run a git command, logging the invocation."""
        cmd = ["git"] + list(args)
        work_dir = cwd or self.project_root
        logger.debug("git %s (cwd=%s)", " ".join(args), work_dir)
        result = subprocess.run(
            cmd,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            logger.debug("git exit=%d stderr=%s", result.returncode, result.stderr.strip())
        return result

    def _branch_exists(self, branch: str) -> bool:
        """Check if a local branch exists."""
        result = self._git("rev-parse", "--verify", f"refs/heads/{branch}")
        return result.returncode == 0

    def _spec_lock(self, spec_id: str):
        """Return a file lock for merge serialization on a spec."""
        import filelock
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        return filelock.FileLock(self.lock_dir / f"merge-{spec_id}.lock", timeout=120)

    def _git_config_lock(self):
        """Repo-wide lock for operations that write to .git/config.

        Git uses an exclusive lock on .git/config for upstream tracking writes.
        Concurrent ``git worktree add -b`` calls from parallel task execution
        race on this lock, causing "could not lock config file" errors.
        Serializing these calls through a filelock avoids the race.
        """
        import filelock
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        return filelock.FileLock(self.lock_dir / "git-config.lock", timeout=120)

    # ------------------------------------------------------------------
    # Agent config propagation
    # ------------------------------------------------------------------

    def _copy_agent_configs(self, worktree_path: Path) -> None:
        """Copy agent/odin config files from project root into the worktree.

        Copies (not symlinks) each path in AGENT_CONFIG_PATHS so that
        ``load_config()`` and agent CLIs find their settings inside the
        worktree checkout. Best-effort: logs on failure, never raises.
        """
        for rel in AGENT_CONFIG_PATHS:
            src = self.project_root / rel
            dst = worktree_path / rel
            if not src.exists() or dst.exists():
                continue
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                if src.is_dir():
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
                logger.debug("Copied agent config %s → %s", rel, worktree_path)
            except OSError:
                logger.debug("Failed to copy agent config %s to worktree", rel)

    def _write_worktree_gitignore(self, worktree_path: Path) -> None:
        """Write/append agent config paths to .gitignore in the worktree.

        Uses ``_GITIGNORE_MARKER`` for idempotency — skips if already present.
        Prevents ``git add -A`` from staging copied config files.
        """
        gi_path = worktree_path / ".gitignore"
        existing = gi_path.read_text() if gi_path.exists() else ""
        if _GITIGNORE_MARKER in existing:
            return

        lines = [
            "",
            _GITIGNORE_MARKER,
            *(f"/{p}" for p in _WORKTREE_GITIGNORE_PATHS),
            "/.gitignore",
            "",
        ]
        try:
            with open(gi_path, "a") as f:
                f.write("\n".join(lines))
            logger.debug("Wrote agent config .gitignore in %s", worktree_path)
        except OSError:
            logger.debug("Failed to write .gitignore in %s", worktree_path)

    def _clean_untracked_agent_configs(self, worktree_path: Path) -> None:
        """Remove untracked agent configs before merge to prevent conflicts.

        Only removes files/dirs that are NOT tracked by git (i.e. our copies).
        Also removes .gitignore if it contains our marker and is untracked.
        """
        paths_to_check = list(AGENT_CONFIG_PATHS) + [".gitignore"]
        for rel in paths_to_check:
            target = worktree_path / rel
            if not target.exists():
                continue
            # Check if tracked
            check = self._git(
                "ls-files", "--error-unmatch", rel, cwd=worktree_path,
            )
            if check.returncode == 0:
                # Tracked by git — leave it alone
                continue
            # For .gitignore, only remove if it has our marker
            if rel == ".gitignore":
                content = target.read_text()
                if _GITIGNORE_MARKER not in content:
                    continue
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
                logger.debug("Cleaned untracked %s from %s", rel, worktree_path)
            except OSError:
                logger.debug("Failed to clean %s from %s", rel, worktree_path)

    # ------------------------------------------------------------------
    # Spec branch
    # ------------------------------------------------------------------

    def create_spec_branch(self, spec_id: str, base_branch: str = "main") -> str:
        """Create spec/<spec_id> from base_branch. Returns branch name.

        Idempotent: if the branch already exists, returns its name.
        """
        branch = f"spec/{spec_id}"
        if self._branch_exists(branch):
            logger.info("Spec branch %s already exists, skipping creation", branch)
            return branch

        # Try to fetch latest from remote (best-effort)
        self._git("fetch", "origin", base_branch)

        # Check if the repo has any commits at all
        head_check = self._git("rev-parse", "HEAD")
        if head_check.returncode != 0:
            # Empty repo — create an initial commit so branches can exist
            logger.info("Empty repo detected, creating initial commit for worktree isolation")
            self._git("commit", "--allow-empty", "-m", "Initial commit (odin worktree setup)")

        # Create branch from the base. Prefer origin/<base> if it exists,
        # otherwise fall back to local <base>, then current HEAD.
        ref_check = self._git("rev-parse", "--verify", f"origin/{base_branch}")
        if ref_check.returncode == 0:
            base_ref = f"origin/{base_branch}"
        else:
            local_check = self._git("rev-parse", "--verify", base_branch)
            if local_check.returncode == 0:
                base_ref = base_branch
            else:
                # base_branch doesn't exist (e.g. config says "main" but repo has "master")
                base_ref = "HEAD"
                logger.warning("Base branch %s not found, using HEAD", base_branch)

        result = self._git("branch", branch, base_ref)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to create spec branch {branch}: {result.stderr}")

        # Push to remote (best-effort — local-first)
        push = self._git("push", "-u", "origin", branch)
        if push.returncode != 0:
            logger.warning("Failed to push spec branch %s to remote: %s", branch, push.stderr.strip())

        logger.info("Created spec branch: %s from %s", branch, base_ref)
        return branch

    # ------------------------------------------------------------------
    # Spec worktree
    # ------------------------------------------------------------------

    def create_spec_worktree(
        self,
        spec_id: str,
        post_hooks: Optional[List[str]] = None,
        symlinks: Optional[List[str]] = None,
    ) -> Path:
        """Create a worktree for the spec branch itself.

        Lives at {worktree_base}/{spec_id}/_spec — the _spec subdirectory
        won't conflict with numeric task IDs.

        Idempotent: if the worktree already exists, returns its path.
        """
        spec_branch = f"spec/{spec_id}"
        worktree_path = self.worktree_base / spec_id / "_spec"

        if worktree_path.exists() and (worktree_path / ".git").exists():
            logger.info("Spec worktree already exists at %s, skipping creation", worktree_path)
            return worktree_path

        # Clean up stale directory (exists but no .git — e.g. previous failed attempt)
        if worktree_path.exists():
            self._git("worktree", "remove", "--force", str(worktree_path))
            if worktree_path.exists():
                shutil.rmtree(worktree_path, ignore_errors=True)

        worktree_path.parent.mkdir(parents=True, exist_ok=True)

        result = self._git("worktree", "add", str(worktree_path), spec_branch)
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to create spec worktree for {spec_id}: {result.stderr}"
            )

        # Propagate agent/odin configs so CLIs find their settings
        self._copy_agent_configs(worktree_path)
        self._write_worktree_gitignore(worktree_path)

        # Create symlinks from project root (best-effort)
        for rel_path in (symlinks or []):
            src = self.project_root / rel_path
            dst = worktree_path / rel_path
            if src.exists() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    dst.symlink_to(src)
                except OSError:
                    logger.warning("Failed to symlink %s -> %s", dst, src)

        # Run post-create hooks (best-effort)
        for hook_cmd in (post_hooks or []):
            try:
                subprocess.run(
                    hook_cmd,
                    shell=True,
                    cwd=worktree_path,
                    timeout=120,
                    capture_output=True,
                )
            except Exception:
                logger.warning("Post-create hook failed in %s: %s", worktree_path, hook_cmd)

        logger.info("Created spec worktree: %s (branch=%s)", worktree_path, spec_branch)
        return worktree_path

    def update_spec_worktree(self, spec_id: str) -> None:
        """Update the spec worktree to reflect the latest spec branch state.

        Called after a task merge updates the spec branch. Runs
        git reset --hard to pick up the new merge commit.

        Best-effort: logs a warning on failure, never raises.
        """
        worktree_path = self.worktree_base / spec_id / "_spec"
        if not worktree_path.exists():
            logger.debug("No spec worktree at %s, skipping update", worktree_path)
            return

        spec_branch = f"spec/{spec_id}"
        result = self._git("reset", "--hard", spec_branch, cwd=worktree_path)
        if result.returncode != 0:
            logger.warning(
                "Failed to update spec worktree at %s: %s",
                worktree_path, result.stderr.strip(),
            )
        else:
            logger.info("Updated spec worktree at %s to latest %s", worktree_path, spec_branch)

    # ------------------------------------------------------------------
    # Task worktree
    # ------------------------------------------------------------------

    def create_task_worktree(
        self,
        spec_id: str,
        task_id: str,
        post_hooks: Optional[List[str]] = None,
        symlinks: Optional[List[str]] = None,
    ) -> Path:
        """Create a worktree for a task, branched from the spec branch.

        Idempotent: if the worktree already exists, returns its path.
        """
        spec_branch = f"spec/{spec_id}"
        task_branch = f"task/{spec_id}/{task_id}"
        worktree_path = self.worktree_base / spec_id / str(task_id)

        # If worktree already exists, return it
        if worktree_path.exists() and (worktree_path / ".git").exists():
            logger.info("Worktree already exists at %s, skipping creation", worktree_path)
            return worktree_path

        # Ensure spec branch is up-to-date (picks up merged upstream work)
        self._git("fetch", "origin", spec_branch)

        # Prefer the remote tracking ref if available
        ref_check = self._git("rev-parse", "--verify", f"origin/{spec_branch}")
        base_ref = f"origin/{spec_branch}" if ref_check.returncode == 0 else spec_branch

        # Create parent directory
        worktree_path.parent.mkdir(parents=True, exist_ok=True)

        # Serialize worktree creation: ``git worktree add -b`` from a remote
        # tracking ref writes upstream config to .git/config. Concurrent calls
        # race on git's internal config lock and fail with "could not lock
        # config file .git/config: File exists".
        with self._git_config_lock():
            # If the task branch already exists (e.g., from a previous failed attempt),
            # create worktree from the existing branch
            if self._branch_exists(task_branch):
                result = self._git("worktree", "add", str(worktree_path), task_branch)
            else:
                result = self._git(
                    "worktree", "add",
                    "-b", task_branch,
                    str(worktree_path),
                    base_ref,
                )

        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to create worktree for task {task_id}: {result.stderr}"
            )

        # Propagate agent/odin configs so CLIs find their settings
        self._copy_agent_configs(worktree_path)
        self._write_worktree_gitignore(worktree_path)

        # Create symlinks from project root (best-effort)
        for rel_path in (symlinks or []):
            src = self.project_root / rel_path
            dst = worktree_path / rel_path
            if src.exists() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    dst.symlink_to(src)
                except OSError:
                    logger.warning("Failed to symlink %s -> %s", dst, src)

        # Run post-create hooks (best-effort)
        for hook_cmd in (post_hooks or []):
            try:
                subprocess.run(
                    hook_cmd,
                    shell=True,
                    cwd=worktree_path,
                    timeout=120,
                    capture_output=True,
                )
            except Exception:
                logger.warning("Post-create hook failed in %s: %s", worktree_path, hook_cmd)

        logger.info("Created task worktree: %s (branch=%s)", worktree_path, task_branch)
        return worktree_path

    def get_worktree_path(self, spec_id: str, task_id: str) -> Path:
        """Return the path where a task's worktree would be."""
        return self.worktree_base / spec_id / str(task_id)

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def _auto_commit_worktree(self, spec_id: str, task_id: str, task_title: str = "") -> bool:
        """Commit any uncommitted changes in a task worktree before merging.

        Safety net for agents that write files but forget to git commit.
        Returns True if a commit was created, False if nothing to commit.
        """
        worktree_path = self.get_worktree_path(spec_id, task_id)
        if not worktree_path.exists():
            return False

        # Check for any changes (staged, unstaged, or untracked)
        status = self._git("status", "--porcelain", cwd=worktree_path)
        if not status.stdout.strip():
            return False

        logger.warning(
            "Task %s has uncommitted changes in worktree — auto-committing",
            task_id,
        )

        # Stage everything and commit
        self._git("add", "-A", cwd=worktree_path)

        # Unstage worktree-local files we never want committed
        self._git("reset", "--", ".gitignore", ".odin/", cwd=worktree_path)

        # If nothing is left staged after unstaging .gitignore, bail out
        staged = self._git("diff", "--cached", "--name-only", cwd=worktree_path)
        if not staged.stdout.strip():
            return False

        msg = f"Auto-commit task {task_id}: {task_title}" if task_title else f"Auto-commit task {task_id}"
        result = self._git("commit", "-m", msg, cwd=worktree_path)
        if result.returncode != 0:
            logger.warning("Auto-commit failed for task %s: %s", task_id, result.stderr.strip())
            return False

        logger.info("Auto-committed uncommitted work for task %s", task_id)
        return True

    def merge_task_into_spec(
        self,
        spec_id: str,
        task_id: str,
        task_title: str = "",
    ) -> MergeResult:
        """Merge a task branch into the spec branch.

        Auto-commits any uncommitted work in the task worktree first,
        then uses a file lock to serialize merges within the same spec.
        """
        spec_branch = f"spec/{spec_id}"
        task_branch = f"task/{spec_id}/{task_id}"

        if not self._branch_exists(task_branch):
            return MergeResult(success=False, error=f"Task branch {task_branch} does not exist")

        # Safety net: commit any uncommitted work before merging
        self._auto_commit_worktree(spec_id, task_id, task_title)

        lock = self._spec_lock(spec_id)
        try:
            with lock:
                return self._do_merge(spec_branch, task_branch, task_id, task_title)
        except Exception as exc:
            logger.exception("Merge failed for task %s into spec %s", task_id, spec_id)
            return MergeResult(success=False, error=str(exc))

    def _do_merge(
        self,
        spec_branch: str,
        task_branch: str,
        task_id: str,
        task_title: str,
    ) -> MergeResult:
        """Perform the actual merge (called under lock).

        Prefers the _spec worktree if it exists (the spec branch is already
        checked out there). Falls back to creating a temporary merge worktree
        for specs that predate the _spec worktree feature.
        """
        spec_id = spec_branch.removeprefix("spec/")
        spec_wt = self.worktree_base / spec_id / "_spec"
        use_spec_wt = spec_wt.exists() and (spec_wt / ".git").exists()

        if use_spec_wt:
            return self._merge_in_worktree(spec_wt, spec_branch, task_branch, task_id, task_title)

        # Fallback: create a temporary merge worktree (older specs without _spec)
        merge_wt = self.worktree_base / "_merge" / f"spec_{spec_id}"
        if merge_wt.exists():
            self._git("worktree", "remove", "--force", str(merge_wt))

        try:
            merge_wt.parent.mkdir(parents=True, exist_ok=True)
            result = self._git("worktree", "add", str(merge_wt), spec_branch)
            if result.returncode != 0:
                return MergeResult(
                    success=False,
                    error=f"worktree add failed: {result.stderr}",
                )
            return self._merge_in_worktree(merge_wt, spec_branch, task_branch, task_id, task_title)
        finally:
            if merge_wt.exists():
                self._git("worktree", "remove", "--force", str(merge_wt))

    def _merge_in_worktree(
        self,
        worktree: Path,
        spec_branch: str,
        task_branch: str,
        task_id: str,
        task_title: str,
    ) -> MergeResult:
        """Merge a task branch into the spec branch inside the given worktree."""
        # Fetch and pull latest spec branch
        self._git("fetch", "origin", spec_branch)
        self._git("pull", "origin", spec_branch, cwd=worktree)

        # Capture diff stat before merge (spec..task shows what the task added)
        diff_stat_result = self._git(
            "diff", "--stat", f"{spec_branch}...{task_branch}", cwd=worktree,
        )
        diff_stat = diff_stat_result.stdout.strip() if diff_stat_result.returncode == 0 else None

        # Detect no-op: if the task branch has no commits beyond the spec branch,
        # there's nothing to merge. Don't lie about it.
        if not diff_stat:
            logger.warning(
                "Task %s has no changes vs spec branch — nothing to merge", task_id,
            )
            return MergeResult(success=True, noop=True)

        # Clean untracked agent configs to prevent merge conflicts
        self._clean_untracked_agent_configs(worktree)

        # Merge task branch
        msg = f"Merge task {task_id}: {task_title}" if task_title else f"Merge task {task_id}"
        result = self._git("merge", "--no-ff", task_branch, "-m", msg, cwd=worktree)
        if result.returncode != 0:
            status = self._git("status", "--porcelain", cwd=worktree)
            has_conflicts = any(
                line.startswith("UU") or line.startswith("AA") or line.startswith("DD")
                for line in status.stdout.splitlines()
            )
            if has_conflicts:
                self._git("merge", "--abort", cwd=worktree)
                return MergeResult(
                    success=False,
                    conflict=True,
                    error=f"Merge conflict: {result.stderr.strip()}",
                    diff_stat=diff_stat,
                )
            return MergeResult(success=False, error=f"merge failed: {result.stderr}", diff_stat=diff_stat)

        # Push updated spec branch
        push = self._git("push", "origin", spec_branch, cwd=worktree)
        if push.returncode != 0:
            logger.warning("Failed to push spec branch after merge: %s", push.stderr.strip())

        logger.info("Merged %s into %s", task_branch, spec_branch)
        return MergeResult(success=True, diff_stat=diff_stat)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup_task_worktree(self, spec_id: str, task_id: str) -> None:
        """Remove a task's worktree and delete its branch."""
        worktree_path = self.get_worktree_path(spec_id, task_id)
        task_branch = f"task/{spec_id}/{task_id}"

        if worktree_path.exists():
            result = self._git("worktree", "remove", "--force", str(worktree_path))
            if result.returncode != 0:
                logger.warning("Failed to remove worktree %s: %s", worktree_path, result.stderr.strip())

        if self._branch_exists(task_branch):
            self._git("branch", "-D", task_branch)

        logger.info("Cleaned up task worktree: %s", task_branch)

    def remove_task_worktree(self, spec_id: str, task_id: str) -> None:
        """Remove a task's worktree but preserve its branch.

        Used on task failure so committed work survives for recovery.
        """
        worktree_path = self.get_worktree_path(spec_id, task_id)
        task_branch = f"task/{spec_id}/{task_id}"

        if worktree_path.exists():
            result = self._git("worktree", "remove", "--force", str(worktree_path))
            if result.returncode != 0:
                logger.warning("Failed to remove worktree %s: %s", worktree_path, result.stderr.strip())

        logger.info("Removed task worktree (branch preserved): %s", task_branch)

    def finalize_spec(self, spec_id: str) -> None:
        """Clean up all worktrees for a spec. Does NOT merge to main (that's a PR)."""
        spec_dir = self.worktree_base / spec_id
        if spec_dir.exists():
            for task_dir in spec_dir.iterdir():
                if task_dir.is_dir():
                    self._git("worktree", "remove", "--force", str(task_dir))
            # Remove the spec directory itself if empty
            try:
                spec_dir.rmdir()
            except OSError:
                pass

        logger.info("Finalized spec %s: cleaned up worktrees", spec_id)

    def cleanup_all(self) -> None:
        """Remove all worktrees managed by this WorktreeManager."""
        if self.worktree_base.exists():
            for spec_dir in self.worktree_base.iterdir():
                if spec_dir.is_dir():
                    self.finalize_spec(spec_dir.name)

        # Prune stale worktree refs
        self._git("worktree", "prune")
        logger.info("Cleaned up all worktrees")

    # ------------------------------------------------------------------
    # PR creation
    # ------------------------------------------------------------------

    def create_spec_pr(
        self,
        spec_id: str,
        title: str,
        task_summaries: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Create a GitHub PR from the spec branch to main.

        Requires `gh` CLI to be installed and authenticated.
        Returns the PR URL on success, None on failure.
        """
        spec_branch = f"spec/{spec_id}"

        # Build PR body
        body_lines = [f"## Spec: {title}", ""]
        if task_summaries:
            body_lines.append("### Tasks")
            for summary in task_summaries:
                body_lines.append(f"- {summary}")
            body_lines.append("")
        body_lines.append("Generated by `odin spec finalize`")
        body = "\n".join(body_lines)

        try:
            result = subprocess.run(
                [
                    "gh", "pr", "create",
                    "--base", "main",
                    "--head", spec_branch,
                    "--title", title,
                    "--body", body,
                ],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                pr_url = result.stdout.strip()
                logger.info("Created PR for spec %s: %s", spec_id, pr_url)
                return pr_url
            else:
                stderr = result.stderr.strip()
                logger.warning("Failed to create PR for spec %s: %s", spec_id, stderr)
                raise RuntimeError(stderr or "gh pr create failed with no output")
        except FileNotFoundError:
            logger.warning("gh CLI not found — cannot create PR for spec %s", spec_id)
            return None
        except Exception:
            logger.exception("Failed to create PR for spec %s", spec_id)
            return None
