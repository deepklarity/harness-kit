"""Tests for advisor-trial wiring inside exec_task (W7 routing-and-cost).

Tags: [mock] — no real HTTP, no real LLM.

AdvisorWatcher's own consult/cap/refusal behavior is covered directly in
tests/disk/test_advisor.py. These tests verify the orchestrator side of the
wiring: the watcher is only started for trial-agent runs, the prompt gets
the consult-protocol section, and consult tokens land on the task's cost
record via the same aggregation used for retries.
"""

import asyncio
from unittest.mock import patch

import pytest

from odin import advisor
from odin.models import AdvisorConfig, AgentConfig, CostTier, OdinConfig
from odin.orchestrator import Orchestrator


class FakeWatcher:
    """Stand-in for AdvisorWatcher: skips real polling, seeds finished consults."""

    instances = []

    def __init__(self, working_dir, consult_fn, max_consults=2, poll_interval=2.0, task_title=""):
        self.working_dir = working_dir
        self.consult_fn = consult_fn
        self.max_consults = max_consults
        self.task_title = task_title
        self.consults = [
            advisor.ConsultResult(question="Q1", answer="Use approach B.", token_usage={"total_tokens": 111}),
            advisor.ConsultResult(question="Q2", answer="Use approach C.", token_usage={"total_tokens": 222}),
        ]
        self.refused_count = 1
        self.stopped = False
        FakeWatcher.instances.append(self)

    def stop(self):
        self.stopped = True

    async def run(self):
        await asyncio.sleep(0)


@pytest.fixture(autouse=True)
def _reset_fake_watcher_instances():
    FakeWatcher.instances = []
    yield
    FakeWatcher.instances = []


def _base_agents():
    return {
        "mock": AgentConfig(cli_command="mock", capabilities=["coding"], cost_tier=CostTier.LOW, enabled=True),
        "claude": AgentConfig(cli_command="claude", capabilities=["coding"], cost_tier=CostTier.HIGH, enabled=True),
    }


@pytest.fixture
def config_advisor_enabled_for_mock(odin_dirs):
    return OdinConfig(
        base_agent="mock",
        board_backend="local",
        task_storage=str(odin_dirs["tasks"]),
        log_dir=str(odin_dirs["logs"]),
        cost_storage=str(odin_dirs["costs"]),
        agents=_base_agents(),
        advisor=AdvisorConfig(
            enabled=True, agent="claude", model="claude-sonnet-5", max_consults=2, trial_agents=["mock"],
        ),
    )


@pytest.fixture
def config_advisor_enabled_but_not_trialing_mock(odin_dirs):
    return OdinConfig(
        base_agent="mock",
        board_backend="local",
        task_storage=str(odin_dirs["tasks"]),
        log_dir=str(odin_dirs["logs"]),
        cost_storage=str(odin_dirs["costs"]),
        agents=_base_agents(),
        advisor=AdvisorConfig(
            enabled=True, agent="claude", model="claude-sonnet-5", max_consults=2,
            trial_agents=["glm", "minimax"],
        ),
    )


@pytest.fixture
def config_advisor_disabled(odin_dirs):
    return OdinConfig(
        base_agent="mock",
        board_backend="local",
        task_storage=str(odin_dirs["tasks"]),
        log_dir=str(odin_dirs["logs"]),
        cost_storage=str(odin_dirs["costs"]),
        agents=_base_agents(),
        advisor=AdvisorConfig(enabled=False, trial_agents=["mock"]),
    )


class TestAdvisorWatcherGating:
    def test_watcher_starts_for_trial_agent(self, odin_dirs, config_advisor_enabled_for_mock):
        orch = Orchestrator(config=config_advisor_enabled_for_mock)
        task = orch.task_mgr.create_task(title="Fix flaky test", description="Repro and fix", spec_id=None)
        orch.task_mgr.assign_task(task.id, "mock")

        with patch("odin.advisor.AdvisorWatcher", FakeWatcher):
            result = asyncio.run(orch.exec_task(task.id))

        assert result["success"] is True
        assert len(FakeWatcher.instances) == 1

    def test_watcher_not_started_when_agent_outside_trial_roster(
        self, odin_dirs, config_advisor_enabled_but_not_trialing_mock,
    ):
        orch = Orchestrator(config=config_advisor_enabled_but_not_trialing_mock)
        task = orch.task_mgr.create_task(title="Fix flaky test", description="Repro and fix", spec_id=None)
        orch.task_mgr.assign_task(task.id, "mock")

        with patch("odin.advisor.AdvisorWatcher", FakeWatcher):
            result = asyncio.run(orch.exec_task(task.id))

        assert result["success"] is True
        assert FakeWatcher.instances == []

    def test_watcher_not_started_when_advisor_disabled(self, odin_dirs, config_advisor_disabled):
        orch = Orchestrator(config=config_advisor_disabled)
        task = orch.task_mgr.create_task(title="Fix flaky test", description="Repro and fix", spec_id=None)
        orch.task_mgr.assign_task(task.id, "mock")

        with patch("odin.advisor.AdvisorWatcher", FakeWatcher):
            result = asyncio.run(orch.exec_task(task.id))

        assert result["success"] is True
        assert FakeWatcher.instances == []


class TestAdvisorPromptInjection:
    def test_prompt_gets_consult_protocol_for_trial_agent(self, odin_dirs, config_advisor_enabled_for_mock):
        orch = Orchestrator(config=config_advisor_enabled_for_mock)
        task = orch.task_mgr.create_task(title="Fix flaky test", description="Repro and fix", spec_id=None)
        orch.task_mgr.assign_task(task.id, "mock")

        captured_prompts = []
        original_execute = None
        from odin.harnesses.mock import MockHarness

        async def capturing_execute(self, prompt, context):
            captured_prompts.append(prompt)
            return await original_execute(self, prompt, context)

        original_execute = MockHarness.execute

        with patch("odin.advisor.AdvisorWatcher", FakeWatcher), \
                patch.object(MockHarness, "execute", new=capturing_execute):
            result = asyncio.run(orch.exec_task(task.id))

        assert result["success"] is True
        assert len(captured_prompts) == 1
        assert "Advisor consult" in captured_prompts[0]
        assert ".odin/advice_request.md" in captured_prompts[0]

    def test_prompt_omits_consult_protocol_when_not_trialing(
        self, odin_dirs, config_advisor_enabled_but_not_trialing_mock,
    ):
        orch = Orchestrator(config=config_advisor_enabled_but_not_trialing_mock)
        task = orch.task_mgr.create_task(title="Fix flaky test", description="Repro and fix", spec_id=None)
        orch.task_mgr.assign_task(task.id, "mock")

        captured_prompts = []
        from odin.harnesses.mock import MockHarness
        original_execute = MockHarness.execute

        async def capturing_execute(self, prompt, context):
            captured_prompts.append(prompt)
            return await original_execute(self, prompt, context)

        with patch("odin.advisor.AdvisorWatcher", FakeWatcher), \
                patch.object(MockHarness, "execute", new=capturing_execute):
            result = asyncio.run(orch.exec_task(task.id))

        assert result["success"] is True
        assert "Advisor consult" not in captured_prompts[0]


class TestAdvisorCostRecording:
    def test_consult_tokens_land_on_the_task_cost_record(self, odin_dirs, config_advisor_enabled_for_mock):
        """Acceptance: tokens from the consult round trip appear on the task."""
        orch = Orchestrator(config=config_advisor_enabled_for_mock)
        task = orch.task_mgr.create_task(title="Fix flaky test", description="Repro and fix", spec_id=None)
        orch.task_mgr.assign_task(task.id, "mock")

        with patch("odin.advisor.AdvisorWatcher", FakeWatcher):
            result = asyncio.run(orch.exec_task(task.id))

        assert result["success"] is True

        records = orch.cost_store.load_by_spec("_orphan")
        task_records = [r for r in records if r.task_id == task.id]
        # Main execution record + 2 advisor consult records (FakeWatcher seeds 2)
        assert len(task_records) == 3
        advisor_records = [r for r in task_records if r.agent == "advisor"]
        assert len(advisor_records) == 2
        assert {r.total_tokens for r in advisor_records} == {111, 222}
        assert all(r.model == "claude-sonnet-5" for r in advisor_records)

    def test_no_advisor_cost_records_when_not_trialing(
        self, odin_dirs, config_advisor_enabled_but_not_trialing_mock,
    ):
        orch = Orchestrator(config=config_advisor_enabled_but_not_trialing_mock)
        task = orch.task_mgr.create_task(title="Fix flaky test", description="Repro and fix", spec_id=None)
        orch.task_mgr.assign_task(task.id, "mock")

        with patch("odin.advisor.AdvisorWatcher", FakeWatcher):
            result = asyncio.run(orch.exec_task(task.id))

        assert result["success"] is True
        records = orch.cost_store.load_by_spec("_orphan")
        task_records = [r for r in records if r.task_id == task.id]
        assert len(task_records) == 1  # only the main execution record
        assert all(r.agent != "advisor" for r in task_records)
