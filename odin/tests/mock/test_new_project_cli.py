"""End-to-end CLI tests for `odin new-project` against a scratch git repo,
with the TaskIt backend and doctor mocked out (no network, no real agent probes).
"""

import subprocess
from pathlib import Path

import pytest
import yaml

from odin.cli import OdinCLI
from odin.taskit.models import TaskStatus


def _git(args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


class FakeTaskItBackend:
    """Records calls; stands in for TaskItBackend so tests need no server."""

    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._board_id = kwargs.get("board_id")
        self.create_board_calls = []
        self.saved_specs = []
        self.created_tasks = {}
        self.updated_tasks = {}
        FakeTaskItBackend.instances.append(self)

    def create_board(self, name, working_dir, disabled_agents=None):
        self.create_board_calls.append(
            {"name": name, "working_dir": working_dir, "disabled_agents": disabled_agents}
        )
        self._board_id = 4242
        return self._board_id

    def save_spec(self, spec):
        self.saved_specs.append(spec)
        return spec

    def create_task(self, task):
        task.id = "faketask01"
        self.created_tasks[task.id] = task
        return task

    def update_task(self, task):
        self.updated_tasks[task.id] = task
        return task

    def load_task(self, task_id):
        return self.created_tasks.get(task_id) or self.updated_tasks.get(task_id)

    def load_all_tasks(self):
        return list(self.created_tasks.values())

    def delete_task(self, task_id):
        return self.created_tasks.pop(task_id, None) is not None

    def get_task_raw(self, task_id):
        return {}


@pytest.fixture(autouse=True)
def _reset_fake_backend_instances():
    FakeTaskItBackend.instances.clear()
    yield
    FakeTaskItBackend.instances.clear()


@pytest.fixture
def scratch_repo(tmp_path):
    """A plain (non-git) scratch project directory to bootstrap."""
    project = tmp_path / "scratch-project"
    project.mkdir()
    (project / "README.md").write_text("# Scratch project\n")
    return project


@pytest.fixture
def patch_backend(monkeypatch):
    monkeypatch.setattr(
        "odin.backends.registry.get_backend",
        lambda name, **kwargs: FakeTaskItBackend(**kwargs),
    )


@pytest.fixture
def patch_doctor(monkeypatch):
    """Doctor runs for real but we stub run_doctor to avoid slow/flaky agent probes."""
    from odin import doctor as doctor_mod

    report = doctor_mod.DoctorReport()

    def _fake_run_doctor(cfg, *, fast=False, merge_queue_name=None, **kwargs):
        return report

    monkeypatch.setattr(doctor_mod, "run_doctor", _fake_run_doctor)
    return report


def test_new_project_scaffolds_git_creates_board_and_dispatchable_task(
    tmp_path, scratch_repo, patch_backend, patch_doctor, capsys
):
    cli = OdinCLI()

    cli.new_project(str(scratch_repo), base_url="http://localhost:9999", fast=True)

    # Git repo was initialized.
    assert (scratch_repo / ".git").exists()

    # Config written for the new project.
    config_path = scratch_repo / ".odin" / "config.yaml"
    assert config_path.exists()
    data = yaml.safe_load(config_path.read_text())
    assert data["board_id"] == 4242
    assert data["base_url"] == "http://localhost:9999"

    # Exactly one board was created, pointed at the resolved project dir.
    backend = FakeTaskItBackend.instances[0]
    assert len(backend.create_board_calls) == 1
    assert backend.create_board_calls[0]["working_dir"] == str(scratch_repo.resolve())

    # A spec and a dispatchable (assigned) task were created.
    assert len(backend.saved_specs) == 1
    assert len(backend.created_tasks) == 1
    task = next(iter(backend.created_tasks.values()))
    assert task.spec_id == backend.saved_specs[0].id

    updated = backend.updated_tasks.get(task.id)
    assert updated is not None
    assert updated.assigned_agent
    assert updated.status == TaskStatus.TODO

    import re
    out = capsys.readouterr().out
    # rich wraps long lines and injects ANSI codes; normalize before asserting
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out).replace("\n", "")
    assert str(scratch_repo) in plain
    assert task.id in plain
    assert "odin exec" in plain


def test_new_project_idempotent_rerun_refuses_politely(
    tmp_path, scratch_repo, patch_backend, patch_doctor, capsys
):
    cli = OdinCLI()
    cli.new_project(str(scratch_repo), base_url="http://localhost:9999", fast=True)
    assert len(FakeTaskItBackend.instances) == 1

    # Re-running without --force must not create a second board/task.
    cli.new_project(str(scratch_repo), base_url="http://localhost:9999", fast=True)

    assert len(FakeTaskItBackend.instances) == 1
    out = capsys.readouterr().out
    assert "already" in out.lower()


def test_new_project_force_rerun_creates_new_board(
    tmp_path, scratch_repo, patch_backend, patch_doctor
):
    cli = OdinCLI()
    cli.new_project(str(scratch_repo), base_url="http://localhost:9999", fast=True)
    assert len(FakeTaskItBackend.instances) == 1

    cli.new_project(str(scratch_repo), base_url="http://localhost:9999", fast=True, force=True)

    assert len(FakeTaskItBackend.instances) == 2


def test_new_project_missing_target_errors_cleanly(tmp_path, patch_backend, patch_doctor):
    cli = OdinCLI()
    missing = tmp_path / "nope"
    with pytest.raises(SystemExit):
        cli.new_project(str(missing), base_url="http://localhost:9999", fast=True)
    assert len(FakeTaskItBackend.instances) == 0


def test_new_project_does_not_commit_on_existing_repos_history(
    tmp_path, patch_backend, patch_doctor
):
    """Safety: new-project must never add commits to a repo that already has git history."""
    project = tmp_path / "existing-repo"
    project.mkdir()
    _git(["init", "-b", "main"], project)
    (project / "a.txt").write_text("a\n")
    _git(["add", "-A"], project)
    _git(["-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-m", "first"], project)

    before = _git(["rev-parse", "HEAD"], project).stdout.strip()

    cli = OdinCLI()
    cli.new_project(str(project), base_url="http://localhost:9999", fast=True)

    after = _git(["rev-parse", "HEAD"], project).stdout.strip()
    assert before == after
