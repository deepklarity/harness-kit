"""Integration-test fixtures (require real CLI agents on PATH)."""

import shutil
import tempfile

import pytest

from odin.models import AgentConfig, CostTier, OdinConfig


@pytest.fixture
def work_dir():
    d = tempfile.mkdtemp(prefix="odin_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _make_config(work_dir: str, base_agent: str = "codex") -> OdinConfig:
    """Build a config using only real, available agents (no claude)."""
    return OdinConfig(
        base_agent=base_agent,
        agents={
            "codex": AgentConfig(
                cli_command="codex",
                capabilities=["reasoning", "planning", "coding", "writing"],
                cost_tier=CostTier.MEDIUM,
            ),
            "gemini": AgentConfig(
                cli_command="gemini",
                capabilities=["coding", "writing", "research"],
                cost_tier=CostTier.LOW,
            ),
            "qwen": AgentConfig(
                cli_command="qwen",
                capabilities=["coding", "writing"],
                cost_tier=CostTier.LOW,
            ),
        },
        task_storage=f"{work_dir}/.odin/tasks",
        log_dir=f"{work_dir}/.odin/logs",
    )
