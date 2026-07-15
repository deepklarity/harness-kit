"""Fable task #360 — run-start one-liner + effective-input dedup (odin side).

The "Effective input" comment was an 8KB inline dump posted once per
attempt — five copies on a task retried five times. It becomes:

  * a one-line, human-readable run-start event ("Run started · agent/model
    · attempt N · full input attached") — under ~200 chars; and
  * the full effective input as a machine comment (``debug:effective_input``,
    hidden behind the machine toggle by default), suppressed when the prompt
    hasn't changed so retries don't stack copies.

Nothing is dropped — the input stays reachable in the machine channel
exactly like an execution trace.

Tags: [mock] — no real HTTP, no real LLM.
"""

import asyncio

import pytest

from odin.models import AgentConfig, CostTier, OdinConfig
from odin.orchestrator import Orchestrator


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


def _run_starts(comments):
    return [c for c in comments if str(c.get("content", "")).startswith("Run started")]


def _input_dumps(comments):
    return [c for c in comments if "debug:effective_input" in (c.get("attachments") or [])]


class TestRunStartOneLiner:
    """The human-visible run-start line."""

    def test_one_liner_under_200_chars(self, odin_dirs, config_with_mock):
        orch = Orchestrator(config=config_with_mock)
        task = orch.task_mgr.create_task(title="T", description="Write a poem", spec_id=None)

        orch._post_run_start(task.id, "glm", "glm-5.2", "the full wrapped prompt body")

        starts = _run_starts(orch.task_mgr.get_comments(task.id))
        assert len(starts) == 1
        body = starts[0]["content"]
        assert len(body) < 200
        assert "glm/glm-5.2" in body
        assert "attempt 1" in body
        assert "full input attached" in body

    def test_attempt_increments_per_run(self, odin_dirs, config_with_mock):
        orch = Orchestrator(config=config_with_mock)
        task = orch.task_mgr.create_task(title="T", description="d", spec_id=None)

        orch._post_run_start(task.id, "glm", "glm-5.2", "prompt")
        orch._post_run_start(task.id, "glm", "glm-5.2", "prompt")

        starts = _run_starts(orch.task_mgr.get_comments(task.id))
        assert len(starts) == 2
        assert "attempt 1" in starts[0]["content"]
        assert "attempt 2" in starts[1]["content"]


class TestEffectiveInputDedup:
    """The 8KB machine dump is suppressed when the prompt hasn't changed."""

    def test_dumped_once_when_prompt_unchanged(self, odin_dirs, config_with_mock):
        orch = Orchestrator(config=config_with_mock)
        task = orch.task_mgr.create_task(title="T", description="d", spec_id=None)

        orch._post_run_start(task.id, "glm", "glm-5.2", "same prompt body")
        orch._post_run_start(task.id, "glm", "glm-5.2", "same prompt body")

        assert len(_input_dumps(orch.task_mgr.get_comments(task.id))) == 1

    def test_redumps_when_prompt_changes(self, odin_dirs, config_with_mock):
        orch = Orchestrator(config=config_with_mock)
        task = orch.task_mgr.create_task(title="T", description="d", spec_id=None)

        orch._post_run_start(task.id, "glm", "glm-5.2", "prompt v1")
        orch._post_run_start(task.id, "glm", "glm-5.2", "prompt v2")

        assert len(_input_dumps(orch.task_mgr.get_comments(task.id))) == 2

    def test_full_input_reachable_in_machine_channel(self, odin_dirs, config_with_mock):
        """Nothing is dropped — the full input lives in the debug comment."""
        orch = Orchestrator(config=config_with_mock)
        task = orch.task_mgr.create_task(title="T", description="d", spec_id=None)

        orch._post_run_start(task.id, "glm", "glm-5.2", "THE FULL WRAPPED PROMPT")

        dumps = _input_dumps(orch.task_mgr.get_comments(task.id))
        assert len(dumps) == 1
        assert "THE FULL WRAPPED PROMPT" in dumps[0]["content"]
        assert dumps[0]["content"].startswith("Effective input")


class TestFullEffectiveInputNoTruncation:
    """The machine dump carries the FULL prompt — no 8 KB cap.

    The run-start line promises "full input attached"; the dump must
    deliver it byte-for-byte. Regression for the slice that cut
    effective_input at 8000 chars while the one-liner still claimed the
    whole input was reachable. A short prompt passes that check vacuously,
    so this drives the cap past where the old slice fell.
    """

    def test_prompt_over_8kb_preserved_in_full(self, odin_dirs, config_with_mock):
        orch = Orchestrator(config=config_with_mock)
        task = orch.task_mgr.create_task(title="T", description="d", spec_id=None)

        tail_marker = "UNIQUE_TAIL_MARKER_ZZZ_END"
        prompt = ("x" * 20000) + tail_marker

        orch._post_run_start(task.id, "glm", "glm-5.2", prompt)

        dumps = _input_dumps(orch.task_mgr.get_comments(task.id))
        assert len(dumps) == 1
        content = dumps[0]["content"]
        assert tail_marker in content, (
            "effective-input dump truncated the prompt — the run-start line "
            "claims 'full input attached' but the tail was dropped"
        )
        assert content == f"Effective input:\n\n{prompt}"

    def test_prompt_over_8kb_still_dedups_when_unchanged(self, odin_dirs, config_with_mock):
        """A large prompt that didn't change between retries adds one dump,
        not one per attempt."""
        orch = Orchestrator(config=config_with_mock)
        task = orch.task_mgr.create_task(title="T", description="d", spec_id=None)

        prompt = "y" * 20000
        orch._post_run_start(task.id, "glm", "glm-5.2", prompt)
        orch._post_run_start(task.id, "glm", "glm-5.2", prompt)

        assert len(_input_dumps(orch.task_mgr.get_comments(task.id))) == 1


class TestRunStartWiredIntoExecTask:
    """exec_task posts the run-start one-liner (not just the old dump)."""

    def test_exec_task_posts_run_start_one_liner(self, odin_dirs, config_with_mock):
        orch = Orchestrator(config=config_with_mock)
        task = orch.task_mgr.create_task(title="T", description="Write a poem", spec_id=None)
        orch.task_mgr.assign_task(task.id, "mock")

        asyncio.run(orch.exec_task(task.id))

        starts = _run_starts(orch.task_mgr.get_comments(task.id))
        assert len(starts) == 1
        assert len(starts[0]["content"]) < 200
