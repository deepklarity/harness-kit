"""Shared fixtures for Odin tests."""

import asyncio
from pathlib import Path
from typing import List
from unittest.mock import MagicMock

import pytest

from odin.models import AgentConfig, CostTier, OdinConfig, TaskResult
from odin.taskit import TaskManager


@pytest.fixture
def odin_dirs(tmp_path):
    """Create a temporary .odin/ directory structure."""
    dirs = {
        "root": tmp_path / ".odin",
        "tasks": tmp_path / ".odin" / "tasks",
        "logs": tmp_path / ".odin" / "logs",
        "specs": tmp_path / ".odin" / "specs",
        "costs": tmp_path / ".odin" / "costs",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


@pytest.fixture
def task_mgr(odin_dirs):
    """Return a TaskManager wired to a temporary directory."""
    return TaskManager(str(odin_dirs["tasks"]))


@pytest.fixture
def make_config():
    """Factory for OdinConfig with sensible defaults and overrides."""

    def _make(**overrides):
        defaults = dict(
            base_agent="claude",
            board_backend="local",
            agents={
                "claude": AgentConfig(
                    cli_command="claude",
                    capabilities=["reasoning", "planning", "coding", "writing"],
                    cost_tier=CostTier.HIGH,
                    default_model="claude-sonnet-4-5",
                    premium_model="claude-opus-4",
                ),
                "gemini": AgentConfig(
                    cli_command="gemini",
                    capabilities=["coding", "writing", "research"],
                    cost_tier=CostTier.LOW,
                    default_model="gemini-2.5-flash",
                    premium_model="gemini-2.5-pro",
                ),
                "qwen": AgentConfig(
                    cli_command="qwen",
                    capabilities=["coding", "writing"],
                    cost_tier=CostTier.LOW,
                    default_model="qwen3-coder",
                    premium_model="qwen3-coder",
                ),
            },
        )
        defaults.update(overrides)
        return OdinConfig(**defaults)

    return _make


class FakeDelayedStdout:
    """Simulates a subprocess that emits lines with delays."""

    def __init__(self, lines: List[bytes], delay: float = 0.05):
        self._lines = list(lines)
        self._delay = delay
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._lines):
            raise StopAsyncIteration
        if self._index > 0:
            await asyncio.sleep(self._delay)
        line = self._lines[self._index]
        self._index += 1
        return line


def make_fake_process(stdout_lines: List[bytes], delay: float = 0.05, returncode: int = 0):
    """Create a mock asyncio.subprocess.Process with fake streaming stdout."""
    proc = MagicMock()
    proc.stdout = FakeDelayedStdout(stdout_lines, delay=delay)
    proc.stderr = MagicMock()

    async def _wait():
        return returncode

    proc.wait = _wait
    proc.pid = 12345
    proc.returncode = returncode
    return proc
