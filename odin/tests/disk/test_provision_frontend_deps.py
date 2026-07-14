"""Disk tests for scripts/provision_frontend_deps.sh.

The provisioning script makes the frontend suite runnable inside a task
worktree by sharing ONE node_modules install (under the main repo's
``.odin/.cache``) across every worktree via a symlink.  Worktrees are born
without ``node_modules`` (gitignored); without provisioning the frontend
gate dies env-only (``vitest: not found``) and promote-check reports
ENV_MISSING — a fake hold on every clean task.

These tests drive the script with a FAKE installer (``FRONTEND_INSTALL_CMD``)
so no real ``npm``/network is needed: the install path is exercised by
having the fake create a marker file inside ``node_modules``.  The live
``npm ci`` is proven by the task-200 proof artifacts (timing + size).
"""

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from odin.worktree import WorktreeManager

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "provision_frontend_deps.sh"


def _run(args, cwd, **kwargs):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, **kwargs)


def _init_repo(path: Path) -> None:
    _run(["git", "init", "-b", "main"], cwd=path)
    _run(["git", "config", "user.email", "test@test.com"], cwd=path)
    _run(["git", "config", "user.name", "Test"], cwd=path)
    (path / "README.md").write_text("# t\n")
    _run(["git", "add", "."], cwd=path)
    _run(["git", "commit", "-m", "init"], cwd=path)


def _add_frontend(repo: Path) -> None:
    """Commit a taskit/taskit-frontend skeleton the worktree can check out."""
    fe = repo / "taskit" / "taskit-frontend"
    fe.mkdir(parents=True)
    (fe / "package.json").write_text('{"name":"t","scripts":{"test:run":"vitest run"}}')
    (fe / "package-lock.json").write_text("{}")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "fe"], cwd=repo)


def _fake_installer(counter: Path, tmp_path: Path) -> str:
    """Write a fake ``npm ci`` script and return its path.

    The production script runs ``$FRONTEND_INSTALL_CMD`` word-split (no
    ``eval``), so the installer must be a single command — exactly like the
    real ``npm ci``.  The fake is a standalone script that builds
    ``node_modules`` with a marker file and bumps a counter (so tests can
    assert the shared cache was installed exactly once).  The counter path
    is passed to the fake via ``$FAKE_INSTALL_COUNTER``.
    """
    script = tmp_path / "fake_installer.sh"
    script.write_text(
        textwrap.dedent(
            f"""
            #!/bin/sh
            set -e
            mkdir -p node_modules
            echo pkg > node_modules/.installed
            if [ -n "$FAKE_INSTALL_COUNTER" ]; then
                echo x >> "$FAKE_INSTALL_COUNTER"
            fi
            """
        ).lstrip()
    )
    script.chmod(0o755)
    return str(script)


def _provision(
    worktree: Path,
    frontend_dir: Path,
    installer: str,
    counter: Path | None = None,
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["FRONTEND_DIR"] = str(frontend_dir)
    env["FRONTEND_INSTALL_CMD"] = installer
    if counter is not None:
        env["FAKE_INSTALL_COUNTER"] = str(counter)
    return _run(["sh", str(SCRIPT)], cwd=worktree, env=env)


@pytest.fixture
def repo_with_fe(tmp_path):
    repo = tmp_path / "main"
    repo.mkdir()
    _init_repo(repo)
    _add_frontend(repo)
    return repo


# ------------------------------------------------------------------
# Shared-cache symlink provisioning
# ------------------------------------------------------------------

class TestProvision:
    def test_symlinks_worktree_to_shared_cache(self, repo_with_fe, tmp_path):
        wt = WorktreeManager(repo_with_fe, worktree_dir=".odin/worktrees")
        wt.create_spec_branch("sp_p", base_branch="main")
        worktree = wt.create_task_worktree("sp_p", "1")
        fe = worktree / "taskit" / "taskit-frontend"
        counter = tmp_path / "installs.txt"
        installer = _fake_installer(counter, tmp_path)

        rc = _provision(worktree, fe, installer, counter)

        assert rc.returncode == 0, rc.stderr
        nm = fe / "node_modules"
        assert nm.is_symlink(), "worktree node_modules must be a symlink"
        # Shared install lives under the MAIN repo's .odin/.cache, not the worktree.
        shared = repo_with_fe / ".odin" / ".cache" / "frontend-node-modules" / "node_modules"
        assert shared.is_dir(), "shared cache must be created under main repo"
        assert (nm / ".installed").read_text().strip() == "pkg"
        assert counter.read_text() == "x\n", "installer must run exactly once"

    def test_idempotent_second_run_is_noop(self, repo_with_fe, tmp_path):
        wt = WorktreeManager(repo_with_fe, worktree_dir=".odin/worktrees")
        wt.create_spec_branch("sp_i", base_branch="main")
        worktree = wt.create_task_worktree("sp_i", "1")
        fe = worktree / "taskit" / "taskit-frontend"
        counter = tmp_path / "installs.txt"
        installer = _fake_installer(counter, tmp_path)

        _provision(worktree, fe, installer, counter)
        rc2 = _provision(worktree, fe, installer, counter)

        assert rc2.returncode == 0
        assert counter.read_text() == "x\n", "second run must not reinstall"

    def test_shared_cache_reused_across_worktrees(self, repo_with_fe, tmp_path):
        wt = WorktreeManager(repo_with_fe, worktree_dir=".odin/worktrees")
        wt.create_spec_branch("sp_r", base_branch="main")
        w1 = wt.create_task_worktree("sp_r", "1")
        w2 = wt.create_task_worktree("sp_r", "2")
        counter = tmp_path / "installs.txt"
        installer = _fake_installer(counter, tmp_path)

        _provision(w1, w1 / "taskit" / "taskit-frontend", installer, counter)
        _provision(w2, w2 / "taskit" / "taskit-frontend", installer, counter)

        nm1 = w1 / "taskit" / "taskit-frontend" / "node_modules"
        nm2 = w2 / "taskit" / "taskit-frontend" / "node_modules"
        # Both symlink to the SAME shared install (one install, two links).
        assert nm1.is_symlink() and nm2.is_symlink()
        assert os.path.realpath(nm1) == os.path.realpath(nm2)
        assert counter.read_text() == "x\n", "shared cache installed once for both"

    def test_real_node_modules_skips_provisioning(self, repo_with_fe, tmp_path):
        """A pre-existing real node_modules dir means deps are already present
        (e.g. a developer ran npm install by hand) — never clobber it."""
        wt = WorktreeManager(repo_with_fe, worktree_dir=".odin/worktrees")
        wt.create_spec_branch("sp_s", base_branch="main")
        worktree = wt.create_task_worktree("sp_s", "1")
        fe = worktree / "taskit" / "taskit-frontend"
        (fe / "node_modules").mkdir(exist_ok=True)
        (fe / "node_modules" / "handmade").write_text("keepme")
        counter = tmp_path / "installs.txt"
        installer = _fake_installer(counter, tmp_path)

        rc = _provision(worktree, fe, installer, counter)

        assert rc.returncode == 0
        assert not (fe / "node_modules").is_symlink(), "must not replace a real dir"
        assert (fe / "node_modules" / "handmade").read_text() == "keepme"
        assert not counter.exists(), "installer must not run when deps present"

    def test_installer_failure_exits_nonzero_and_leaves_no_symlink(self, repo_with_fe, tmp_path):
        wt = WorktreeManager(repo_with_fe, worktree_dir=".odin/worktrees")
        wt.create_spec_branch("sp_f", base_branch="main")
        worktree = wt.create_task_worktree("sp_f", "1")
        fe = worktree / "taskit" / "taskit-frontend"

        rc = _provision(worktree, fe, "exit 1")

        assert rc.returncode != 0, "installer failure must surface as non-zero"
        nm = fe / "node_modules"
        assert not nm.exists(), "no broken/partial symlink left for the suite to trip on"
        # Partial shared cache must be cleaned so the next run retries cleanly.
        shared = repo_with_fe / ".odin" / ".cache" / "frontend-node-modules" / "node_modules"
        assert not shared.exists(), "failed install must not leave a half-built cache"
