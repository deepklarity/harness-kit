"""Real integration tests that invoke actual CLI agents.

Excluded from the default pytest run (requires gemini, qwen, codex CLIs on PATH).
Run explicitly: python -m pytest tests/integration/ -v

Test 1: Harness availability — verify gemini, qwen, codex are on PATH
Test 2: Single harness execute — send a simple prompt to gemini, get real output
Test 3: Decomposition — use codex as base agent to decompose the poem spec into JSON sub-tasks
Test 4: Full e2e — run the full pipeline, produce poem.html in a temp directory
Test 5: Plan only — verify staged plan creates tasks without executing
Test 6: Exec single task — plan first, then exec one task by ID
Test 7: Assemble separately — plan + exec_all + assemble as separate steps
Test 8: Reassign — plan, then reassign a task to a different agent
Test 9: Disk write capability — verify agents can create files on disk
"""

import json
from pathlib import Path

import pytest

from odin.harnesses.registry import get_harness, HARNESS_REGISTRY
from odin.models import AgentConfig, CostTier, OdinConfig
from odin.orchestrator import Orchestrator
from odin.specs import derive_spec_status
from odin.taskit.models import TaskStatus

from .conftest import _make_config


SPEC_PATH = Path(__file__).parent.parent.parent / "specs" / "poem_spec.md"


# ---------------------------------------------------------------------------
# Test 1: Harness availability
# ---------------------------------------------------------------------------
class TestHarnessAvailability:
    """Verify that the CLI agents we depend on are actually installed."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("agent_name,cli_cmd", [
        ("gemini", "gemini"),
        ("qwen", "qwen"),
        ("codex", "codex"),
    ])
    async def test_harness_is_available(self, agent_name, cli_cmd):
        cfg = AgentConfig(cli_command=cli_cmd, capabilities=["writing"])
        harness = get_harness(agent_name, cfg)
        available = await harness.is_available()
        assert available, f"{agent_name} CLI ({cli_cmd}) should be on PATH"

    def test_all_expected_harnesses_registered(self):
        expected = {"claude", "codex", "gemini", "qwen", "minimax", "glm"}
        assert expected.issubset(set(HARNESS_REGISTRY.keys())), (
            f"Missing harnesses: {expected - set(HARNESS_REGISTRY.keys())}"
        )


# ---------------------------------------------------------------------------
# Test 2: Single harness execute — real CLI call
# ---------------------------------------------------------------------------
class TestSingleHarnessExecute:
    """Send a trivial prompt to one agent and verify we get real output."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_gemini_returns_output(self, work_dir):
        cfg = AgentConfig(cli_command="gemini", capabilities=["writing"])
        harness = get_harness("gemini", cfg)

        result = await harness.execute(
            "Respond with exactly: HELLO_ODIN_TEST", {"working_dir": work_dir}
        )

        assert result.success, f"Gemini execute failed: {result.error}"
        assert len(result.output.strip()) > 0, "Gemini should return non-empty output"
        assert result.duration_ms is not None and result.duration_ms > 0
        assert result.agent == "Gemini"

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_qwen_returns_output(self, work_dir):
        cfg = AgentConfig(cli_command="qwen", capabilities=["writing"])
        harness = get_harness("qwen", cfg)

        result = await harness.execute(
            "Respond with exactly: HELLO_ODIN_TEST", {"working_dir": work_dir}
        )

        assert result.success, f"Qwen execute failed: {result.error}"
        assert len(result.output.strip()) > 0, "Qwen should return non-empty output"
        assert result.duration_ms is not None and result.duration_ms > 0
        assert result.agent == "Qwen"


# ---------------------------------------------------------------------------
# Test 3: Decomposition — base agent produces valid JSON sub-tasks
# ---------------------------------------------------------------------------
class TestDecomposition:
    """Use codex as base agent to decompose the poem spec.
    Verify the output is a valid JSON array of sub-tasks with the right shape.
    """

    @pytest.mark.asyncio
    @pytest.mark.timeout(120)
    async def test_decompose_returns_valid_subtasks(self, work_dir):
        config = _make_config(work_dir, base_agent="codex")
        orch = Orchestrator(config)

        spec = SPEC_PATH.read_text()
        sub_tasks = await orch._decompose(spec, work_dir)

        # Must be a non-empty list
        assert isinstance(sub_tasks, list), f"Expected list, got {type(sub_tasks)}"
        assert len(sub_tasks) >= 2, (
            f"Spec asks for multiple agents; got {len(sub_tasks)} sub-tasks"
        )

        # Each sub-task must have required fields
        for i, st in enumerate(sub_tasks):
            assert "title" in st, f"Sub-task {i} missing 'title'"
            assert "description" in st, f"Sub-task {i} missing 'description'"
            assert len(st["description"]) > 10, (
                f"Sub-task {i} description too short: {st['description']!r}"
            )


# ---------------------------------------------------------------------------
# Test 4: Full e2e — decompose, dispatch, assemble poem.html
# ---------------------------------------------------------------------------
class TestFullPoemE2E:
    """Run the complete orchestration pipeline and verify poem.html output."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(300)
    async def test_poem_html_generated(self, work_dir):
        config = _make_config(work_dir, base_agent="codex")
        orch = Orchestrator(config)

        spec = SPEC_PATH.read_text()
        result = await orch.run(spec, working_dir=work_dir)

        # 1. poem.html must exist
        poem_path = Path(work_dir) / "poem.html"
        assert poem_path.exists(), (
            f"poem.html not found in {work_dir}. Orchestrator output:\n{result[:500]}"
        )

        html = poem_path.read_text()

        # 2. Must be valid HTML structure
        assert "<!DOCTYPE html>" in html, "Missing DOCTYPE"
        assert "<html" in html, "Missing <html> tag"
        assert "</html>" in html, "Missing closing </html>"

        # 3. Must contain <mark>AgentName</mark> tags from at least 2 agents
        import re
        marks = re.findall(r"<mark>(.*?)</mark>", html, re.IGNORECASE)
        assert len(marks) >= 2, (
            f"Expected at least 2 <mark> agent names, found {len(marks)}: {marks}"
        )

        # 4. Tasks should be tracked in .odin/tasks/
        tasks = orch.task_mgr.list_tasks()
        assert len(tasks) >= 2, f"Expected >=2 tasks, got {len(tasks)}"

        completed = [t for t in tasks if t.status == TaskStatus.DONE]
        assert len(completed) >= 2, (
            f"Expected >=2 completed tasks, got {len(completed)}. "
            f"Statuses: {[(t.id[:8], t.status.value, t.assigned_agent) for t in tasks]}"
        )

        # 5. Structured logs should exist
        log_dir = Path(work_dir) / ".odin" / "logs"
        log_files = list(log_dir.glob("run_*.jsonl"))
        assert len(log_files) >= 1, "No log files found"

        # Verify log entries are valid JSON
        with open(log_files[0]) as f:
            entries = [json.loads(line) for line in f if line.strip()]
        actions = [e["action"] for e in entries]
        assert "run_started" in actions, "Missing run_started log"
        assert "run_completed" in actions, "Missing run_completed log"
        assert "task_assigned" in actions, "Missing task_assigned log"

        # 6. Spec archive should exist (run.json is gone)
        spec_dir = Path(work_dir) / ".odin" / "specs"
        assert spec_dir.exists(), "Spec archive directory not found"
        spec_files = list(spec_dir.glob("sp_*.json"))
        assert len(spec_files) >= 1, "No spec archive files found"

        # 7. All tasks should have spec_id
        for t in tasks:
            assert t.spec_id is not None, f"Task {t.id[:8]} missing spec_id"

        # 8. run.json should NOT exist
        manifest_path = Path(work_dir) / ".odin" / "run.json"
        assert not manifest_path.exists(), "run.json should not exist anymore"

        # 9. Print summary for manual inspection
        print(f"\n--- poem.html ({len(html)} chars) ---")
        print(html[:1000])
        print(f"\n--- Tasks ---")
        for t in tasks:
            print(f"  {t.id[:8]} | {t.status.value:12} | {t.assigned_agent:8} | {t.title} | spec={t.spec_id}")
        print(f"\n--- Log entries: {len(entries)} ---")
        for e in entries:
            print(f"  {e['action']:25} {e.get('agent', ''):10} {e.get('task_id', '')[:8]}")


# ---------------------------------------------------------------------------
# Test 5: Plan only — staged workflow step 1
# ---------------------------------------------------------------------------
class TestPlanOnly:
    """Call plan(), verify tasks created with ASSIGNED status,
    verify spec archive exists, verify nothing executed."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(120)
    async def test_plan_creates_tasks_without_executing(self, work_dir):
        config = _make_config(work_dir, base_agent="codex")
        orch = Orchestrator(config)
        spec = SPEC_PATH.read_text()

        spec_id, tasks = await orch.plan(spec, working_dir=work_dir, spec_file="poem_spec.md")

        # Spec ID should start with sp_
        assert spec_id.startswith("sp_"), f"Spec ID should start with sp_, got {spec_id}"

        # Tasks must exist
        assert len(tasks) >= 2, f"Expected >=2 planned tasks, got {len(tasks)}"

        # All tasks should be ASSIGNED (suggested default), not COMPLETED
        for t in tasks:
            assert t.status == TaskStatus.TODO, (
                f"Task {t.id[:8]} should be ASSIGNED, got {t.status.value}"
            )
            assert t.assigned_agent is not None, (
                f"Task {t.id[:8]} should have a suggested agent"
            )
            assert t.result is None, (
                f"Task {t.id[:8]} should have no result (not executed)"
            )
            assert t.spec_id == spec_id, (
                f"Task {t.id[:8]} should have spec_id={spec_id}, got {t.spec_id}"
            )

        # Verify reasoning exists in task metadata
        has_reasoning = False
        for t in tasks:
            if t.metadata and t.metadata.get("reasoning"):
                has_reasoning = True
                break
        assert has_reasoning, (
            "At least one task should have 'reasoning' in metadata from the decomposition"
        )

        # Spec archive must exist
        spec_archive = orch.spec_store.load(spec_id)
        assert spec_archive is not None, "Spec archive not created"
        assert spec_archive.source == "poem_spec.md"
        assert not spec_archive.abandoned

        # run.json must NOT exist
        manifest_path = Path(work_dir) / ".odin" / "run.json"
        assert not manifest_path.exists(), "run.json should not be created"

        # Print for inspection
        print(f"\n--- Planned {len(tasks)} tasks (spec {spec_id}) ---")
        for t in tasks:
            reasoning = t.metadata.get("reasoning", "-") if t.metadata else "-"
            print(f"  {t.id} | {t.status.value:10} | {t.assigned_agent:8} | {t.title}")
            print(f"    Reasoning: {reasoning}")


# ---------------------------------------------------------------------------
# Test 6: Exec single task — plan + exec one
# ---------------------------------------------------------------------------
class TestExecSingleTask:
    """Plan first, then exec one task by ID, verify only that one completes."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(180)
    async def test_exec_single_task_by_id(self, work_dir):
        config = _make_config(work_dir, base_agent="codex")
        orch = Orchestrator(config)
        spec = SPEC_PATH.read_text()

        # Plan
        spec_id, tasks = await orch.plan(spec, working_dir=work_dir)
        assert len(tasks) >= 2

        # Execute only the first task
        target_id = tasks[0].id
        result = await orch.exec_task(target_id, working_dir=work_dir)

        assert result["success"], f"Task {target_id[:8]} failed: {result['error']}"
        assert result["task_id"] == target_id

        # Verify only the target task was executed
        updated_target = orch.task_mgr.get_task(target_id)
        assert updated_target.status == TaskStatus.DONE
        assert updated_target.result is not None

        # Other tasks should still be ASSIGNED
        for t in tasks[1:]:
            other = orch.task_mgr.get_task(t.id)
            assert other.status == TaskStatus.TODO, (
                f"Task {t.id[:8]} should still be ASSIGNED, got {other.status.value}"
            )

        # Prefix resolution should work
        prefix = target_id[:4]
        resolved = orch.task_mgr.resolve_task_id(prefix)
        assert resolved == target_id, (
            f"Prefix '{prefix}' should resolve to '{target_id}', got '{resolved}'"
        )

        print(f"\n--- Executed task {target_id[:8]} ---")
        print(f"  Agent: {result['agent']}")
        print(f"  Output preview: {result['output'][:200]}")


# ---------------------------------------------------------------------------
# Test 7: Assemble separately — full staged workflow
# ---------------------------------------------------------------------------
class TestAssembleSeparately:
    """Plan + exec_all as separate steps, verify tasks complete."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(300)
    async def test_staged_plan_exec(self, work_dir):
        config = _make_config(work_dir, base_agent="codex")
        orch = Orchestrator(config)
        spec = SPEC_PATH.read_text()

        # Step 1: Plan
        spec_id, tasks = await orch.plan(spec, working_dir=work_dir)
        assert len(tasks) >= 2

        # Verify spec status is "planned"
        spec_tasks = orch.task_mgr.list_tasks(spec_id=spec_id)
        spec_archive = orch.spec_store.load(spec_id)
        status = derive_spec_status(spec_tasks, spec_archive.abandoned)
        assert status == "planned"

        # Step 2: Execute all
        results = await orch.exec_all(working_dir=work_dir, spec_id=spec_id)
        assert len(results) >= 2
        succeeded = [r for r in results if r["success"]]
        assert len(succeeded) >= 2, (
            f"Expected >=2 successful tasks, got {len(succeeded)}"
        )

        # Verify spec status is now "done" (or "blocked" if a task failed)
        spec_tasks = orch.task_mgr.list_tasks(spec_id=spec_id)
        status = derive_spec_status(spec_tasks, spec_archive.abandoned)
        assert status in ("done", "blocked"), f"Expected done or blocked, got {status}"

        print(f"\n--- Staged workflow complete ---")
        print(f"  Spec: {spec_id}")
        print(f"  Tasks: {len(tasks)}")
        print(f"  Succeeded: {len(succeeded)}")
        print(f"  Derived status: {status}")


# ---------------------------------------------------------------------------
# Test 8: Reassign — change agent assignment before execution
# ---------------------------------------------------------------------------
class TestReassign:
    """Plan, then reassign a task to a different agent, verify it changed."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(120)
    async def test_reassign_changes_agent(self, work_dir):
        config = _make_config(work_dir, base_agent="codex")
        orch = Orchestrator(config)
        spec = SPEC_PATH.read_text()

        # Plan
        spec_id, tasks = await orch.plan(spec, working_dir=work_dir)
        assert len(tasks) >= 2

        target = tasks[0]
        original_agent = target.assigned_agent

        # Pick a different agent
        new_agent = "codex" if original_agent != "codex" else "gemini"

        # Reassign
        updated = orch.task_mgr.assign_task(target.id, new_agent)
        assert updated is not None
        assert updated.assigned_agent == new_agent
        assert updated.status == TaskStatus.TODO

        # Verify persistence
        reloaded = orch.task_mgr.get_task(target.id)
        assert reloaded.assigned_agent == new_agent

        print(f"\n--- Reassigned {target.id[:8]} ---")
        print(f"  {original_agent} → {new_agent}")


# ---------------------------------------------------------------------------
# Test 9: Disk write capability — verify agents can create files
# ---------------------------------------------------------------------------
class TestDiskWriteCapability:
    """Send each harness a prompt that writes a file, then verify the file
    exists on disk.  This catches sandbox / permission issues (e.g. codex
    defaulting to restricted mode)."""

    WRITE_PROMPT_TEMPLATE = (
        "Create a file called {filename} in the current directory. "
        "The file must contain exactly the text: ODIN_WRITE_TEST_{agent}. "
        "Do NOT output anything else besides creating the file."
    )

    @pytest.mark.asyncio
    @pytest.mark.timeout(120)
    async def test_codex_can_write_file(self, work_dir):
        filename = "codex_test_output.txt"
        cfg = AgentConfig(
            cli_command="codex",
            capabilities=["coding"],
            cost_tier=CostTier.MEDIUM,
        )
        harness = get_harness("codex", cfg)

        prompt = self.WRITE_PROMPT_TEMPLATE.format(
            filename=filename, agent="CODEX"
        )
        result = await harness.execute(prompt, {"working_dir": work_dir})

        written = Path(work_dir) / filename
        assert written.exists(), (
            f"codex did not create {filename}. "
            f"success={result.success}, error={result.error}, "
            f"output={result.output[:300]}"
        )
        contents = written.read_text()
        assert "ODIN_WRITE_TEST_CODEX" in contents, (
            f"File contents wrong: {contents!r}"
        )
        print(f"\n  codex wrote {filename}: {contents.strip()!r}")

    @pytest.mark.asyncio
    @pytest.mark.timeout(120)
    async def test_gemini_can_write_file(self, work_dir):
        filename = "gemini_test_output.txt"
        cfg = AgentConfig(
            cli_command="gemini",
            capabilities=["coding"],
            cost_tier=CostTier.LOW,
        )
        harness = get_harness("gemini", cfg)

        prompt = self.WRITE_PROMPT_TEMPLATE.format(
            filename=filename, agent="GEMINI"
        )
        result = await harness.execute(prompt, {"working_dir": work_dir})

        written = Path(work_dir) / filename
        assert written.exists(), (
            f"gemini did not create {filename}. "
            f"success={result.success}, error={result.error}, "
            f"output={result.output[:300]}"
        )
        contents = written.read_text()
        assert "ODIN_WRITE_TEST_GEMINI" in contents, (
            f"File contents wrong: {contents!r}"
        )
        print(f"\n  gemini wrote {filename}: {contents.strip()!r}")

    @pytest.mark.asyncio
    @pytest.mark.timeout(120)
    async def test_qwen_can_write_file(self, work_dir):
        filename = "qwen_test_output.txt"
        cfg = AgentConfig(
            cli_command="qwen",
            capabilities=["coding"],
            cost_tier=CostTier.LOW,
        )
        harness = get_harness("qwen", cfg)

        prompt = self.WRITE_PROMPT_TEMPLATE.format(
            filename=filename, agent="QWEN"
        )
        result = await harness.execute(prompt, {"working_dir": work_dir})

        written = Path(work_dir) / filename
        assert written.exists(), (
            f"qwen did not create {filename}. "
            f"success={result.success}, error={result.error}, "
            f"output={result.output[:300]}"
        )
        contents = written.read_text()
        assert "ODIN_WRITE_TEST_QWEN" in contents, (
            f"File contents wrong: {contents!r}"
        )
        print(f"\n  qwen wrote {filename}: {contents.strip()!r}")
