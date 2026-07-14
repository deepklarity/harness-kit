"""Disk/git-subprocess tests for odin.new_project (no network)."""

import subprocess
from pathlib import Path

import pytest

from odin.new_project import NewProjectError, ensure_git_repo, resolve_project_path


def _git(args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


class TestResolveProjectPathLocal:
    def test_existing_local_directory_resolves(self, tmp_path):
        target_dir = tmp_path / "myrepo"
        target_dir.mkdir()
        resolved = resolve_project_path(str(target_dir), None, tmp_path)
        assert resolved == target_dir.resolve()

    def test_missing_local_path_raises(self, tmp_path):
        missing = tmp_path / "does-not-exist"
        with pytest.raises(NewProjectError):
            resolve_project_path(str(missing), None, tmp_path)

    def test_local_path_that_is_a_file_raises(self, tmp_path):
        f = tmp_path / "not_a_dir.txt"
        f.write_text("hello")
        with pytest.raises(NewProjectError):
            resolve_project_path(str(f), None, tmp_path)


class TestResolveProjectPathClone:
    def test_clones_local_git_url_into_derived_dest(self, tmp_path):
        # Use a local bare-ish repo as the "remote" so no network is needed.
        source = tmp_path / "source-repo"
        source.mkdir()
        _git(["init", "-b", "main"], source)
        (source / "README.md").write_text("hello\n")
        _git(["add", "-A"], source)
        _git(["-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-m", "init"], source)

        cwd = tmp_path / "workspace"
        cwd.mkdir()
        # file:// URL isn't matched by is_remote_url's scheme list, so use an
        # absolute path styled like a clone target via the --dest override
        # instead of relying on scheme sniffing here.
        dest = cwd / "cloned"
        resolved = resolve_project_path(f"file://{source}", str(dest), cwd)
        assert resolved == dest.resolve()
        assert (resolved / ".git").exists()
        assert (resolved / "README.md").exists()

    def test_clone_into_existing_destination_refuses(self, tmp_path):
        source = tmp_path / "source-repo"
        source.mkdir()
        _git(["init", "-b", "main"], source)
        (source / "README.md").write_text("hello\n")
        _git(["add", "-A"], source)
        _git(["-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-m", "init"], source)

        cwd = tmp_path / "workspace"
        cwd.mkdir()
        dest = cwd / "already-here"
        dest.mkdir()

        with pytest.raises(NewProjectError):
            resolve_project_path(f"file://{source}", str(dest), cwd)

    def test_clone_failure_raises_new_project_error(self, tmp_path):
        cwd = tmp_path / "workspace"
        cwd.mkdir()
        dest = cwd / "cloned"
        with pytest.raises(NewProjectError):
            resolve_project_path("file:///no/such/repo/at/all", str(dest), cwd)


class TestEnsureGitRepo:
    def test_initializes_git_when_missing(self, tmp_path):
        target = tmp_path / "plain-dir"
        target.mkdir()
        (target / "hello.txt").write_text("hi\n")

        created = ensure_git_repo(target)

        assert created is True
        assert (target / ".git").exists()
        log = _git(["log", "--oneline"], target)
        assert len(log.stdout.strip().splitlines()) == 1

    def test_noop_when_already_a_git_repo(self, tmp_path):
        target = tmp_path / "existing-repo"
        target.mkdir()
        _git(["init", "-b", "main"], target)
        (target / "a.txt").write_text("a\n")
        _git(["add", "-A"], target)
        _git(["-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-m", "first"], target)

        created = ensure_git_repo(target)

        assert created is False
        log = _git(["log", "--oneline"], target)
        # Still exactly one commit — ensure_git_repo must not touch an
        # existing repo's history.
        assert len(log.stdout.strip().splitlines()) == 1
