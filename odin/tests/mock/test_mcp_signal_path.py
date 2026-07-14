"""MCP explicit-signal channel for the evidence ladder (task #331).

The evidence ladder advertises three explicit-signal channels — the stdout
ODIN-STATUS block, a status file, and an MCP status. The first two were
consumed by the orchestrator; this suite pins the third.

An agent that finishes through the TaskIt MCP tools posts a ``proof``
comment ("The proof comment IS your completion message"). That comment is
its completion signal and must be believed as an explicit SUCCESS verdict
when the fragile stdout tail is missing. Without this channel a run that
confirmed done out-of-band via MCP — but whose stdout was truncated and
whose work is not a git delta — would wrongly FAIL, which is the exact
class of loss task #331 exists to stop.

The signal is scoped to THIS run: a ``proof`` comment a *prior* attempt
left on the same task must not masquerade as the current run's verdict
(the same run-scoping the worktree-delta channel gets from the start HEAD).
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


class TestReadMcpSignal:
    """The channel reader in isolation."""

    def test_proof_comment_reads_as_success(self, config_with_mock):
        orch = Orchestrator(config=config_with_mock)
        task = orch.task_mgr.create_task(title="t", description="d", spec_id=None)
        orch.task_mgr.add_comment(
            task.id, "mock", "Done — proof at .proof/...", comment_type="proof"
        )
        assert orch._read_mcp_signal(task.id) is True

    def test_no_proof_comment_is_none(self, config_with_mock):
        """A non-proof comment (status update, question) is not a verdict —
        the channel stays silent so the ladder falls through to the
        worktree evidence."""
        orch = Orchestrator(config=config_with_mock)
        task = orch.task_mgr.create_task(title="t", description="d", spec_id=None)
        orch.task_mgr.add_comment(
            task.id, "mock", "working on it", comment_type="status_update"
        )
        assert orch._read_mcp_signal(task.id) is None

    def test_stale_proof_before_baseline_is_ignored(self, config_with_mock):
        """A proof comment left by a PRIOR attempt (present before this run
        started) must not be read as this run's verdict — only a proof
        posted after the run's comment baseline counts."""
        orch = Orchestrator(config=config_with_mock)
        task = orch.task_mgr.create_task(title="t", description="d", spec_id=None)
        orch.task_mgr.add_comment(
            task.id, "mock", "old attempt's proof", comment_type="proof"
        )
        baseline = orch._count_task_comments(task.id)

        # This run posted nothing new — the stale proof is invisible.
        assert orch._read_mcp_signal(task.id, baseline=baseline) is None

        # This run posts its own proof — now it IS believed.
        orch.task_mgr.add_comment(
            task.id, "mock", "this run's proof", comment_type="proof"
        )
        assert orch._read_mcp_signal(task.id, baseline=baseline) is True


class TestMcpSignalThroughExecTask:
    """The channel wired into the full evidence ladder."""

    def test_mcp_proof_without_marker_or_work_lands_in_review(
        self, config_with_mock, worktree: Path
    ):
        """The decisive case: no stdout ODIN-STATUS block, no git delta —
        the ONLY positive evidence is a proof comment posted via MCP. The
        run must be believed (routed to REVIEW), not FAILED."""
        orch = Orchestrator(config=config_with_mock)
        task = orch.task_mgr.create_task(
            title="confirm via mcp", description="do it", spec_id=None
        )
        orch.task_mgr.assign_task(task.id, "mock")

        async def proof_via_mcp_no_marker(self, prompt, context):
            # The agent confirms completion out-of-band via the MCP proof
            # comment, but its stdout tail lacks the ODIN-STATUS block and
            # it left no git delta the worktree channel could see.
            orch.task_mgr.add_comment(
                task.id, "mock", "Finished. Proof at .proof/.", comment_type="proof"
            )
            return TaskResult(
                success=False,  # stdout-tail verdict — must NOT decide
                output="I'm done; proof is on the board.",
                error="Agent did not emit an ODIN-STATUS block.",
                duration_ms=8.0,
                agent="Mock",
            )

        with patch("odin.harnesses.mock.MockHarness.execute", new=proof_via_mcp_no_marker):
            result = asyncio.run(orch.exec_task(task.id, working_dir=str(worktree)))

        refreshed = orch.task_mgr.get_task(task.id)
        assert refreshed.status == TaskStatus.REVIEW, refreshed.status
        assert result["success"] is True
        # An explicit MCP verdict is a clean success, not an inferred
        # unconfirmed completion.
        assert not (refreshed.metadata or {}).get("unconfirmed_completion")

    def test_no_marker_no_work_no_proof_still_fails(
        self, config_with_mock, worktree: Path
    ):
        """Guard the negative: without ANY explicit signal (no marker, no
        proof comment) and no work, the run still fails — the MCP channel
        must not manufacture a verdict out of nothing."""
        orch = Orchestrator(config=config_with_mock)
        task = orch.task_mgr.create_task(
            title="nothing", description="x", spec_id=None
        )
        orch.task_mgr.assign_task(task.id, "mock")

        async def clean_but_empty(self, prompt, context):
            return TaskResult(
                success=False,
                output="",
                error="Agent produced no output.",
                duration_ms=5.0,
                agent="Mock",
            )

        with patch("odin.harnesses.mock.MockHarness.execute", new=clean_but_empty):
            result = asyncio.run(orch.exec_task(task.id, working_dir=str(worktree)))

        refreshed = orch.task_mgr.get_task(task.id)
        assert refreshed.status == TaskStatus.FAILED, refreshed.status
        assert result["success"] is False
