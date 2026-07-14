"""Pure-function tests for odin.new_project helpers (no disk, no network)."""

from pathlib import Path

import pytest

from odin.new_project import (
    already_initialized,
    is_remote_url,
    repo_name_from_target,
)


class TestIsRemoteUrl:
    @pytest.mark.parametrize(
        "target",
        [
            "https://github.com/me/myrepo.git",
            "https://github.com/me/myrepo",
            "http://example.com/repo.git",
            "git@github.com:me/myrepo.git",
            "ssh://git@example.com/me/myrepo.git",
        ],
    )
    def test_recognizes_remote_urls(self, target):
        assert is_remote_url(target) is True

    @pytest.mark.parametrize(
        "target",
        [
            "../existing-repo",
            "/abs/path/to/repo",
            "relative/repo",
            ".",
            "myrepo",
        ],
    )
    def test_recognizes_local_paths(self, target):
        assert is_remote_url(target) is False


class TestRepoNameFromTarget:
    def test_strips_git_suffix(self):
        assert repo_name_from_target("https://github.com/me/myrepo.git") == "myrepo"

    def test_strips_trailing_slash(self):
        assert repo_name_from_target("https://github.com/me/myrepo/") == "myrepo"

    def test_ssh_style_url(self):
        assert repo_name_from_target("git@github.com:me/myrepo.git") == "myrepo"

    def test_local_path(self):
        assert repo_name_from_target("../existing-repo") == "existing-repo"


class TestAlreadyInitialized:
    def test_false_when_no_odin_dir(self, tmp_path):
        assert already_initialized(tmp_path) is False

    def test_false_when_odin_dir_but_no_config(self, tmp_path):
        (tmp_path / ".odin").mkdir()
        assert already_initialized(tmp_path) is False

    def test_true_when_config_exists(self, tmp_path):
        odin_dir = tmp_path / ".odin"
        odin_dir.mkdir()
        (odin_dir / "config.yaml").write_text("board_backend: taskit\n")
        assert already_initialized(tmp_path) is True
