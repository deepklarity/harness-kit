"""Tests for DAG validation and wave-based execution ordering.

Tags: [simple] — pure logic, no LLM or subprocess calls.
"""

import pytest

from odin.models import AgentConfig, CostTier, OdinConfig
from odin.orchestrator import Orchestrator
from odin.taskit import TaskManager
from odin.taskit.models import TaskStatus


def _make_orchestrator(tmp_path):
    """Build an Orchestrator with minimal config pointed at tmp dirs."""
    task_dir = str(tmp_path / "tasks")
    log_dir = str(tmp_path / "logs")
    cost_dir = str(tmp_path / "costs")
    cfg = OdinConfig(
        base_agent="claude",
        task_storage=task_dir,
        log_dir=log_dir,
        cost_storage=cost_dir,
        board_backend="local",
        agents={
            "claude": AgentConfig(
                cli_command="claude",
                capabilities=["planning"],
                cost_tier=CostTier.HIGH,
            ),
        },
    )
    return Orchestrator(cfg)


# ── DAG validation (cycle detection) ─────────────────────────────────


class TestDAGValidation:
    def test_no_deps_valid(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        t1 = orch.task_mgr.create_task("A", "a")
        t2 = orch.task_mgr.create_task("B", "b")
        # No exception
        orch._validate_dag([t1.id, t2.id])

    def test_linear_chain_valid(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        t1 = orch.task_mgr.create_task("A", "a")
        t2 = orch.task_mgr.create_task("B", "b")
        t3 = orch.task_mgr.create_task("C", "c")

        # A -> B -> C
        task2 = orch.task_mgr.get_task(t2.id)
        task2.depends_on = [t1.id]
        orch.task_mgr._store.save(task2)

        task3 = orch.task_mgr.get_task(t3.id)
        task3.depends_on = [t2.id]
        orch.task_mgr._store.save(task3)

        orch._validate_dag([t1.id, t2.id, t3.id])

    def test_diamond_deps_valid(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        t1 = orch.task_mgr.create_task("Root", "r")
        t2 = orch.task_mgr.create_task("Left", "l")
        t3 = orch.task_mgr.create_task("Right", "r")
        t4 = orch.task_mgr.create_task("Merge", "m")

        # Diamond: t1 -> t2, t1 -> t3, t2 -> t4, t3 -> t4
        for tid, deps in [(t2.id, [t1.id]), (t3.id, [t1.id]), (t4.id, [t2.id, t3.id])]:
            task = orch.task_mgr.get_task(tid)
            task.depends_on = deps
            orch.task_mgr._store.save(task)

        orch._validate_dag([t1.id, t2.id, t3.id, t4.id])

    def test_simple_cycle_detected(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        t1 = orch.task_mgr.create_task("A", "a")
        t2 = orch.task_mgr.create_task("B", "b")

        # A -> B -> A (cycle)
        task1 = orch.task_mgr.get_task(t1.id)
        task1.depends_on = [t2.id]
        orch.task_mgr._store.save(task1)

        task2 = orch.task_mgr.get_task(t2.id)
        task2.depends_on = [t1.id]
        orch.task_mgr._store.save(task2)

        with pytest.raises(RuntimeError, match="cycle"):
            orch._validate_dag([t1.id, t2.id])

    def test_self_cycle_detected(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        t1 = orch.task_mgr.create_task("Self", "s")

        task = orch.task_mgr.get_task(t1.id)
        task.depends_on = [t1.id]
        orch.task_mgr._store.save(task)

        with pytest.raises(RuntimeError, match="cycle"):
            orch._validate_dag([t1.id])

    def test_three_node_cycle_detected(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        t1 = orch.task_mgr.create_task("A", "a")
        t2 = orch.task_mgr.create_task("B", "b")
        t3 = orch.task_mgr.create_task("C", "c")

        # A -> B -> C -> A
        for tid, dep in [(t1.id, t3.id), (t2.id, t1.id), (t3.id, t2.id)]:
            task = orch.task_mgr.get_task(tid)
            task.depends_on = [dep]
            orch.task_mgr._store.save(task)

        with pytest.raises(RuntimeError, match="cycle"):
            orch._validate_dag([t1.id, t2.id, t3.id])

    def test_empty_task_list_valid(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        orch._validate_dag([])


# ── Wave grouping (ready tasks) ──────────────────────────────────────


class TestWaveGrouping:
    def test_independent_tasks_all_in_first_wave(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        t1 = orch.task_mgr.create_task("A", "a")
        t2 = orch.task_mgr.create_task("B", "b")
        t3 = orch.task_mgr.create_task("C", "c")

        for t in [t1, t2, t3]:
            orch.task_mgr.assign_task(t.id, "claude")

        ready = orch.task_mgr.get_ready_tasks([t1.id, t2.id, t3.id])
        assert len(ready) == 3

    def test_chain_one_task_per_wave(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        t1 = orch.task_mgr.create_task("A", "a")
        t2 = orch.task_mgr.create_task("B", "b")
        t3 = orch.task_mgr.create_task("C", "c")

        for t in [t1, t2, t3]:
            orch.task_mgr.assign_task(t.id, "claude")

        # Chain: A -> B -> C
        task2 = orch.task_mgr.get_task(t2.id)
        task2.depends_on = [t1.id]
        orch.task_mgr._store.save(task2)

        task3 = orch.task_mgr.get_task(t3.id)
        task3.depends_on = [t2.id]
        orch.task_mgr._store.save(task3)

        # Wave 1: only A is ready
        ready = orch.task_mgr.get_ready_tasks([t1.id, t2.id, t3.id])
        assert len(ready) == 1
        assert ready[0].id == t1.id

        # Complete A -> Wave 2: B is ready
        orch.task_mgr.update_status(t1.id, TaskStatus.DONE)
        ready = orch.task_mgr.get_ready_tasks([t1.id, t2.id, t3.id])
        assert len(ready) == 1
        assert ready[0].id == t2.id

    def test_mixed_ready_and_blocked(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        t1 = orch.task_mgr.create_task("Independent", "i")
        t2 = orch.task_mgr.create_task("Dep", "d")
        t3 = orch.task_mgr.create_task("Blocker", "b")

        for t in [t1, t2, t3]:
            orch.task_mgr.assign_task(t.id, "claude")

        # t2 depends on t3
        task2 = orch.task_mgr.get_task(t2.id)
        task2.depends_on = [t3.id]
        orch.task_mgr._store.save(task2)

        ready = orch.task_mgr.get_ready_tasks([t1.id, t2.id, t3.id])
        ready_ids = {t.id for t in ready}
        assert t1.id in ready_ids
        assert t3.id in ready_ids
        assert t2.id not in ready_ids


# ── Orchestrator helpers ──────────────────────────────────────────────


class TestParseEnvelope:
    def test_success_envelope(self):
        output = "Some work done\n-------ODIN-STATUS-------\nSUCCESS\n-------ODIN-SUMMARY-------\nAll tasks completed"
        clean, success, summary = Orchestrator._parse_envelope(output)
        assert clean == "Some work done"
        assert success is True
        assert summary == "All tasks completed"

    def test_failed_envelope(self):
        output = "Error occurred\n-------ODIN-STATUS-------\nFAILED\n-------ODIN-SUMMARY-------\nCould not compile"
        clean, success, summary = Orchestrator._parse_envelope(output)
        assert clean == "Error occurred"
        assert success is False
        assert summary == "Could not compile"

    def test_no_envelope(self):
        output = "Plain output with no envelope"
        clean, success, summary = Orchestrator._parse_envelope(output)
        assert clean == output
        assert success is None
        assert summary is None

    def test_status_only_no_summary(self):
        """Envelope with status but no summary section returns summary=None."""
        output = "Done.\n\n-------ODIN-STATUS-------\nSUCCESS"
        clean, success, summary = Orchestrator._parse_envelope(output)
        assert clean == "Done."
        assert success is True
        assert summary is None

    def test_wrap_prompt(self):
        wrapped = Orchestrator._wrap_prompt("Do something")
        assert "Do something" in wrapped
        assert "ODIN-STATUS" in wrapped
        assert "ODIN-SUMMARY" in wrapped

    def test_wrap_prompt_without_mcp_omits_mcp_section(self):
        """When mcp_task_id is None, no MCP guidance appears."""
        wrapped = Orchestrator._wrap_prompt("Do something", mcp_task_id=None)
        assert "TaskIt MCP Tools" not in wrapped
        assert "taskit_add_comment" not in wrapped
        # Core envelope still present
        assert "ODIN-STATUS" in wrapped

    def test_wrap_prompt_with_mcp_includes_mcp_section(self):
        """When mcp_task_id is provided, MCP guidance is injected."""
        wrapped = Orchestrator._wrap_prompt("Do something", mcp_task_id="abc-123")
        assert "## TaskIt MCP Tools" in wrapped
        assert "Your task ID is: abc-123" in wrapped
        assert "taskit_add_comment" in wrapped
        assert "status_update" in wrapped
        assert "question" in wrapped
        assert "proof" in wrapped
        # ODIN-STATUS envelope still present after MCP section
        assert "ODIN-STATUS" in wrapped

    def test_wrap_prompt_mcp_section_between_prompt_and_envelope(self):
        """MCP section comes after the task prompt but before ODIN-STATUS."""
        wrapped = Orchestrator._wrap_prompt("Do something", mcp_task_id="task-42")
        task_idx = wrapped.index("Do something")
        mcp_idx = wrapped.index("TaskIt MCP Tools")
        status_idx = wrapped.index("ODIN-STATUS")
        assert task_idx < mcp_idx < status_idx

    def test_wrap_prompt_with_working_dir_and_mcp(self):
        """Working dir, MCP section, and envelope all compose together."""
        wrapped = Orchestrator._wrap_prompt(
            "Do something", working_dir="/tmp/work", mcp_task_id="task-99"
        )
        assert "Working directory: /tmp/work" in wrapped
        assert "TaskIt MCP Tools" in wrapped
        assert "task-99" in wrapped
        assert "ODIN-STATUS" in wrapped
