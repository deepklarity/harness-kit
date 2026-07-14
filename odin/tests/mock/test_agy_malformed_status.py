"""Harness-level tests for the ODIN-STATUS malformed-block recovery (task #237).

The agent emitted a literal ``\\`` for the ODIN-STATUS value on task
#234 and the run FAILED despite committed work. The harness now
forwards the worktree path to the parser and propagates an
inference marker via TaskResult.metadata so the orchestrator can
record the malformed block in the ErrorEvent ledger (W6.5).
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import List, Optional
from unittest.mock import patch

import pytest

from odin.harnesses.agy import AgyHarness
from odin.models import AgentConfig


class _FakeStream:
    def __init__(self, lines: List[bytes], delay: float = 0):
        self._lines = list(lines)
        self._delay = delay
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._lines):
            raise StopAsyncIteration
        if self._index > 0 and self._delay:
            await asyncio.sleep(self._delay)
        line = self._lines[self._index]
        self._index += 1
        return line


class _FakeStderr:
    def __init__(self, content: bytes = b""):
        self.content = content

    async def read(self):
        return self.content


class _FakeProcess:
    def __init__(
        self,
        returncode: int = 0,
        stdout_lines: Optional[List[bytes]] = None,
        stderr_content: bytes = b"",
        pid: int = 91234,
    ):
        self.returncode = returncode
        self.pid = pid
        self.kill_called = False
        self.wait_called = False
        self.communicate_called = False
        self.stdout = _FakeStream(stdout_lines or [])
        self.stderr = _FakeStderr(stderr_content)

    async def wait(self):
        self.wait_called = True
        return self.returncode

    def kill(self):
        self.kill_called = True
        self.returncode = -9

    async def communicate(self):
        self.communicate_called = True
        all_lines = []
        async for raw in self.stdout:
            all_lines.append(raw)
        return (b"".join(all_lines), self.stderr.content)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def worktree_with_commit(tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    wt.mkdir()
    _git(wt, "init", "-q", "-b", "main")
    _git(wt, "config", "user.email", "t@t")
    _git(wt, "config", "user.name", "t")
    (wt / "README").write_text("seed")
    _git(wt, "add", "README")
    _git(wt, "commit", "-q", "-m", "seed")
    _git(wt, "checkout", "-q", "-b", "task/237")
    (wt / "feature.py").write_text("def f(): return 1\n")
    _git(wt, "add", "feature.py")
    _git(wt, "commit", "-q", "-m", "real work")
    return wt


@pytest.fixture
def clean_worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    wt.mkdir()
    _git(wt, "init", "-q", "-b", "main")
    _git(wt, "config", "user.email", "t@t")
    _git(wt, "config", "user.name", "t")
    (wt / "f").write_text("x")
    _git(wt, "add", "f")
    _git(wt, "commit", "-q", "-m", "seed")
    return wt


class TestAgyMalformedStatus:
    """Pin the harness contract: when the agent emits a malformed
    ODIN-STATUS block, the harness must consult the worktree and
    propagate the verdict + a ``malformed_status`` marker through
    TaskResult.metadata.
    """

    @pytest.mark.asyncio
    async def test_malformed_block_with_committed_work_returns_success(
        self, worktree_with_commit: Path
    ):
        cfg = AgentConfig(cli_command="agy")
        harness = AgyHarness(cfg)

        # The exact symptom observed on task #234.
        stdout = (
            "Did the work.\n"
            "-------ODIN-STATUS-------\n"
            "\\\n"
            "-------ODIN-SUMMARY-------\n"
            "Tried to emit a backslash.\n"
        )
        proc = _FakeProcess(
            returncode=0,
            stdout_lines=[stdout.encode("utf-8")],
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await harness.execute(
                "task", {"working_dir": str(worktree_with_commit)}
            )

        assert result.success is True
        assert result.error is None
        # The inference marker is visible to the orchestrator.
        meta = result.metadata.get("malformed_status")
        assert meta is not None
        assert meta["raw_block"] == "\\"
        assert meta["inferred"] is True
        assert "commit" in meta["inference_reason"].lower()

    @pytest.mark.asyncio
    async def test_malformed_block_with_clean_worktree_fails(
        self, clean_worktree: Path
    ):
        cfg = AgentConfig(cli_command="agy")
        harness = AgyHarness(cfg)

        stdout = (
            "Tried to emit but lost it.\n"
            "-------ODIN-STATUS-------\n"
            "\\\n"
        )
        proc = _FakeProcess(
            returncode=0,
            stdout_lines=[stdout.encode("utf-8")],
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await harness.execute(
                "task", {"working_dir": str(clean_worktree)}
            )

        assert result.success is False
        meta = result.metadata.get("malformed_status")
        assert meta is not None
        assert meta["raw_block"] == "\\"
        assert meta["inferred"] is False

    @pytest.mark.asyncio
    async def test_well_formed_success_has_no_malformed_marker(
        self, worktree_with_commit: Path
    ):
        """Regression guard: a well-formed SUCCESS block must NOT
        populate ``malformed_status`` — the orchestrator only
        records malformed blocks, not every run.
        """
        cfg = AgentConfig(cli_command="agy")
        harness = AgyHarness(cfg)

        stdout = (
            "ok\n"
            "-------ODIN-STATUS-------\n"
            "SUCCESS\n"
            "-------ODIN-SUMMARY-------\n"
            "shipped\n"
        )
        proc = _FakeProcess(
            returncode=0,
            stdout_lines=[stdout.encode("utf-8")],
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await harness.execute(
                "task", {"working_dir": str(worktree_with_commit)}
            )

        assert result.success is True
        assert "malformed_status" not in result.metadata