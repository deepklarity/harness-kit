"""End-to-end proof of the evidence ladder through the real orchestrator
(task #331).

Unlike the pure decision-table unit tests, these drive the actual
``Orchestrator.exec_task`` path against a REAL git worktree and the local
task backend. The harness returns output with NO ODIN-STATUS block — the
exact task-313 symptom — and we assert the run is routed by the worktree
evidence, not by the missing marker:

- committed work, no marker  → REVIEW, flagged unconfirmed (not FAILED)
- no work, clean exit, no marker → FAILED with a true reason (not "Likely")
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from odin.models import AgentConfig, CostTier, OdinConfig, TaskResult
from odin.orchestrator import Orchestrator
from odin.taskit.models import TaskStatus


@pytest.fixture
def config_with_mock(odin_dirs):
    return OdinConfig(
        base_agent="mock",
        board_backend="local",
        task_storage=str(odin_dirs["tasks"]),
        log_dir=str(odin_dirs["logs"]),
        cost_storage=str(odin_dirs["costs"]),
        agents={
            "mock": AgentConfig(
                cli_command="mock",
                capabilities=["coding"],
                cost_tier=CostTier.LOW,
                enabled=True,
            ),
        },
    )


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    """A clean git worktree standing in for a fresh task worktree."""
    wt = tmp_path / "task_wt"
    wt.mkdir()
    _git(wt, "init", "-q", "-b", "main")
    _git(wt, "config", "user.email", "t@t")
    _git(wt, "config", "user.name", "t")
    (wt / "seed.txt").write_text("seed")
    _git(wt, "add", "seed.txt")
    _git(wt, "commit", "-q", "-m", "seed")
    return wt


def test_committed_work_without_marker_lands_in_review_unconfirmed(
    config_with_mock, worktree: Path
):
    """Done means #1: a run with real commits and NO status line lands in
    REVIEW flagged unconfirmed, not FAILED."""
    orch = Orchestrator(config=config_with_mock)
    task = orch.task_mgr.create_task(title="ship a fix", description="do it", spec_id=None)
    orch.task_mgr.assign_task(task.id, "mock")

    async def commit_then_no_marker(self, prompt, context):
        # The agent does real work and commits it, but its last message
        # lacks the ODIN-STATUS block — exactly task 313's failure mode.
        wd = Path(context["working_dir"])
        (wd / "fix.py").write_text("def fix(): return 42\n")
        _git(wd, "add", "fix.py")
        _git(wd, "commit", "-q", "-m", "feat: real fix")
        return TaskResult(
            success=False,  # harness's stdout-tail verdict — must NOT decide
            output="I finished the fix and committed it.",
            error="Agent did not emit an ODIN-STATUS block. Likely the model truncated.",
            duration_ms=10.0,
            agent="Mock",
        )

    with patch("odin.harnesses.mock.MockHarness.execute", new=commit_then_no_marker):
        result = asyncio.run(orch.exec_task(task.id, working_dir=str(worktree)))

    refreshed = orch.task_mgr.get_task(task.id)
    assert refreshed.status == TaskStatus.REVIEW, refreshed.status
    assert result["success"] is True
    # The start HEAD was recorded in run metadata (task #331 item 1).
    assert (refreshed.metadata or {}).get("worktree_start_head")
    # The reviewer is flagged the completion was inferred from the diff.
    comments = orch.task_mgr.get_comments(task.id)
    bodies = " ".join(
        (c.get("content", "") if isinstance(c, dict) else getattr(c, "content", ""))
        for c in comments
    )
    assert "UNCONFIRMED COMPLETION" in bodies


def test_no_work_clean_exit_without_marker_fails_with_true_reason(
    config_with_mock, worktree: Path
):
    """Done means #2: a run with no work and a clean exit fails with the
    true reason — never a "Likely..." guess."""
    orch = Orchestrator(config=config_with_mock)
    task = orch.task_mgr.create_task(title="do nothing", description="x", spec_id=None)
    orch.task_mgr.assign_task(task.id, "mock")

    async def clean_but_empty(self, prompt, context):
        # Clean exit, no commits, no ODIN-STATUS block.
        return TaskResult(
            success=False,
            output="",
            error="Agent produced no output; likely terminated silently.",
            duration_ms=5.0,
            agent="Mock",
        )

    with patch("odin.harnesses.mock.MockHarness.execute", new=clean_but_empty):
        result = asyncio.run(orch.exec_task(task.id, working_dir=str(worktree)))

    refreshed = orch.task_mgr.get_task(task.id)
    assert refreshed.status == TaskStatus.FAILED, refreshed.status
    assert result["success"] is False
    # The reason is the true, observable fact — not a "Likely..." guess.
    reason = result["error"] or ""
    assert "likely" not in reason.lower()
    assert "no worktree changes" in reason.lower()


def test_explicit_success_block_still_wins(config_with_mock, worktree: Path):
    """Rung 1 unchanged: an explicit SUCCESS verdict is believed."""
    orch = Orchestrator(config=config_with_mock)
    task = orch.task_mgr.create_task(title="ok", description="x", spec_id=None)
    orch.task_mgr.assign_task(task.id, "mock")

    async def explicit_success(self, prompt, context):
        return TaskResult(
            success=True,
            output="done\n-------ODIN-STATUS-------\nSUCCESS\n-------ODIN-SUMMARY-------\nok",
            error=None,
            duration_ms=5.0,
            agent="Mock",
        )

    with patch("odin.harnesses.mock.MockHarness.execute", new=explicit_success):
        result = asyncio.run(orch.exec_task(task.id, working_dir=str(worktree)))

    refreshed = orch.task_mgr.get_task(task.id)
    assert refreshed.status == TaskStatus.REVIEW
    assert result["success"] is True
    # A clean SUCCESS is not flagged unconfirmed.
    assert not (refreshed.metadata or {}).get("unconfirmed_completion")
