"""End-to-end proof of resume-on-truncation through the real orchestrator
(task #332).

Two halves of the round trip, driven through the actual
``Orchestrator.exec_task`` against a REAL git worktree:

1. **Detect** — a run that hits the provider output cap (finish_reason in the
   stream) with work in the worktree and NO ODIN-STATUS verdict routes to
   FAILED as a *resumable* truncation (not REVIEW as an unconfirmed
   completion), posts an operator-visible resume-intent comment, and is
   classified as ``truncation`` so the AUTO_REQUEUE policy requeues it.

2. **Resume** — when the task is flagged pending-resume, the NEXT dispatch
   prepends a host-built resume prompt (git worktree state + the prior
   attempt's trace tail + the verify-first instruction) to the brief, so the
   agent continues the work instead of restarting.
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
    wt = tmp_path / "task_wt"
    wt.mkdir()
    _git(wt, "init", "-q", "-b", "main")
    _git(wt, "config", "user.email", "t@t")
    _git(wt, "config", "user.name", "t")
    (wt / "seed.txt").write_text("seed")
    _git(wt, "add", "seed.txt")
    _git(wt, "commit", "-q", "-m", "seed")
    return wt


# A stream that ended at the output cap — the truncation signal.
_TRUNCATED_STREAM = (
    '{"type":"message_delta","delta":{"stop_reason":"max_tokens"}}\n'
    '{"type":"result","stop_reason":"max_tokens","subtype":"success"}'
)


def test_truncation_with_work_routes_to_resumable_failed(
    config_with_mock, worktree: Path
):
    """Detect half: output cap + work + no marker → FAILED (resumable), with a
    resume-intent comment — NOT a REVIEW unconfirmed completion."""
    orch = Orchestrator(config=config_with_mock)
    task = orch.task_mgr.create_task(
        title="large refactor", description="do a big thing", spec_id=None
    )
    orch.task_mgr.assign_task(task.id, "mock")

    async def truncate_with_work(self, prompt, context):
        wd = Path(context["working_dir"])
        # Real, uncommitted work left behind when the cap was hit mid-edit.
        (wd / "partial.py").write_text("def half():\n    # cut off mid-body\n")
        return TaskResult(
            success=False,
            output=_TRUNCATED_STREAM,
            error="Agent did not emit an ODIN-STATUS block. Likely the model truncated mid-generation.",
            duration_ms=12.0,
            agent="Mock",
        )

    with patch("odin.harnesses.mock.MockHarness.execute", new=truncate_with_work):
        result = asyncio.run(orch.exec_task(task.id, working_dir=str(worktree)))

    refreshed = orch.task_mgr.get_task(task.id)
    # Resumable truncation routes to FAILED so the executor's requeue fires —
    # it must NOT masquerade as a review-worthy completion.
    assert refreshed.status == TaskStatus.FAILED, refreshed.status
    assert result["success"] is False
    # The reason names the output cap / truncation, not a silent hang.
    reason = (result["error"] or "").lower()
    assert "cap" in reason or "truncat" in reason
    # An operator-visible resume-intent comment was posted.
    comments = orch.task_mgr.get_comments(task.id)
    bodies = " ".join(
        (c.get("content", "") if isinstance(c, dict) else getattr(c, "content", ""))
        for c in comments
    )
    assert "TRUNCATED WITH WORK" in bodies


def test_plain_truncation_without_cap_signal_still_reviews(
    config_with_mock, worktree: Path
):
    """Regression guard: without the output-cap finish reason, work + no
    marker keeps the task-#331 behaviour — REVIEW as unconfirmed, not a
    resume. Only a genuine output-cap truncation triggers a resume."""
    orch = Orchestrator(config=config_with_mock)
    task = orch.task_mgr.create_task(title="normal task", description="x", spec_id=None)
    orch.task_mgr.assign_task(task.id, "mock")

    async def work_no_marker_no_cap(self, prompt, context):
        wd = Path(context["working_dir"])
        (wd / "fix.py").write_text("def fix(): return 1\n")
        _git(wd, "add", "fix.py")
        _git(wd, "commit", "-q", "-m", "feat: fix")
        # A normal completion stream (stop_reason=end_turn), no cap hit.
        return TaskResult(
            success=False,
            output='{"type":"result","stop_reason":"end_turn","subtype":"success"}',
            error="Agent did not emit an ODIN-STATUS block.",
            duration_ms=8.0,
            agent="Mock",
        )

    with patch("odin.harnesses.mock.MockHarness.execute", new=work_no_marker_no_cap):
        result = asyncio.run(orch.exec_task(task.id, working_dir=str(worktree)))

    refreshed = orch.task_mgr.get_task(task.id)
    assert refreshed.status == TaskStatus.REVIEW, refreshed.status
    assert result["success"] is True


def test_resume_prompt_injected_when_pending(config_with_mock, worktree: Path):
    """Resume half: a pending-resume task gets a host-built resume prompt
    prepended — assembled from the worktree diff and the prior trace tail —
    with the verify-first instruction, so the agent continues, not restarts."""
    orch = Orchestrator(config=config_with_mock)
    task = orch.task_mgr.create_task(
        title="resume me", description="finish the widget refactor", spec_id=None
    )
    orch.task_mgr.assign_task(task.id, "mock")

    # Simulate the prior attempt's residue: uncommitted work in the worktree,
    # a host-side trace, and the pending-resume flag on the task.
    (worktree / "partial.py").write_text("def half(): return  # WIP from attempt 1\n")
    task = orch.task_mgr.get_task(task.id)
    task.metadata["truncation_resume_pending"] = True
    task.metadata["truncation_resume_count"] = 1
    orch.task_mgr.update_task(task)

    log_dir = Path(config_with_mock.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"task_{task.id}.trace.jsonl").write_text(
        '{"type":"assistant","message":{"content":[{"type":"text",'
        '"text":"I was renaming the widget helper when the stream ended"}]}}\n'
    )

    captured: dict = {}

    async def capture_prompt(self, prompt, context):
        captured["prompt"] = prompt
        return TaskResult(
            success=True,
            output="finished\n-------ODIN-STATUS-------\nSUCCESS\n-------ODIN-SUMMARY-------\nok",
            error=None,
            duration_ms=5.0,
            agent="Mock",
        )

    with patch("odin.harnesses.mock.MockHarness.execute", new=capture_prompt):
        asyncio.run(orch.exec_task(task.id, working_dir=str(worktree)))

    prompt = captured["prompt"]
    # The resume header + verify-first instruction (the anti-mid-edit guard).
    assert "RESUMING A TRUNCATED ATTEMPT" in prompt
    assert "verify the working state first" in prompt.lower()
    # The worktree diff summary — the durable git-derived piece.
    assert "partial.py" in prompt
    # The prior attempt's trace tail — reconstructed host-side.
    assert "renaming the widget helper" in prompt
    # The brief is still present; the resume block augments it, not replaces.
    assert "finish the widget refactor" in prompt
