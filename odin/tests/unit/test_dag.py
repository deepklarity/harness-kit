"""Tests for DAG validation and wave-based execution ordering.

Includes:
  - Integration tests via the Orchestrator (TaskManager-backed).
  - Property-based tests (hypothesis) for the three core DAG invariants:
      1. No ready task has an unmet dependency.
      2. A failed dep never yields a ready dependent.
      3. Cycles are always detected with their full path.
  - Unit tests for odin.dag pure functions.

Tags: [simple] — pure logic, no LLM or subprocess calls.
"""

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from odin.dag import DepStatus, check_dep_status, detect_cycle, filter_ready
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


# ── Unit tests: odin.dag pure functions ──────────────────────────────


class TestCheckDepStatus:
    """Direct tests for the shared check_dep_status function."""

    def _make_task(self, id_, status, depends_on=None):
        class FakeTask:
            pass
        t = FakeTask()
        t.id = id_
        t.status = status
        t.depends_on = depends_on or []
        return t

    def test_no_deps_ready(self):
        result = check_dep_status(
            [],
            get_task_fn=lambda _: None,
            is_complete=lambda t: t.status == "done",
            is_failed=lambda t: t.status == "failed",
        )
        assert result == DepStatus.READY

    def test_all_complete_ready(self):
        tasks = {
            "a": self._make_task("a", "done"),
            "b": self._make_task("b", "done"),
        }
        result = check_dep_status(
            ["a", "b"],
            get_task_fn=lambda id_: tasks.get(id_),
            is_complete=lambda t: t.status == "done",
            is_failed=lambda t: t.status == "failed",
        )
        assert result == DepStatus.READY

    def test_one_failed_blocked(self):
        tasks = {
            "a": self._make_task("a", "done"),
            "b": self._make_task("b", "failed"),
        }
        result = check_dep_status(
            ["a", "b"],
            get_task_fn=lambda id_: tasks.get(id_),
            is_complete=lambda t: t.status == "done",
            is_failed=lambda t: t.status == "failed",
        )
        assert result == DepStatus.BLOCKED

    def test_one_incomplete_waiting(self):
        tasks = {
            "a": self._make_task("a", "done"),
            "b": self._make_task("b", "in_progress"),
        }
        result = check_dep_status(
            ["a", "b"],
            get_task_fn=lambda id_: tasks.get(id_),
            is_complete=lambda t: t.status == "done",
            is_failed=lambda t: t.status == "failed",
        )
        assert result == DepStatus.WAITING

    def test_unknown_dep_treated_as_unmet(self):
        result = check_dep_status(
            ["unknown_id"],
            get_task_fn=lambda _: None,
            is_complete=lambda t: t.status == "done",
            is_failed=lambda t: t.status == "failed",
        )
        assert result == DepStatus.WAITING

    def test_failed_takes_priority_over_waiting(self):
        tasks = {
            "a": self._make_task("a", "failed"),
            "b": self._make_task("b", "in_progress"),
        }
        result = check_dep_status(
            ["a", "b"],
            get_task_fn=lambda id_: tasks.get(id_),
            is_complete=lambda t: t.status == "done",
            is_failed=lambda t: t.status == "failed",
        )
        assert result == DepStatus.BLOCKED


class TestDetectCycle:
    """Unit tests for odin.dag.detect_cycle."""

    def _make_tasks(self, dep_map):
        """dep_map: {id: [dep_id, ...]}. Returns a dict of fake tasks."""
        tasks = {}
        for tid, deps in dep_map.items():
            class FakeTask:
                pass
            t = FakeTask()
            t.id = tid
            t.depends_on = deps
            t.title = f"Task {tid}"
            tasks[tid] = t
        return tasks

    def _get_fn(self, tasks):
        return lambda id_: tasks.get(id_)

    def test_no_deps_no_cycle(self):
        tasks = self._make_tasks({"a": [], "b": []})
        assert detect_cycle(["a", "b"], self._get_fn(tasks)) is None

    def test_linear_chain_no_cycle(self):
        tasks = self._make_tasks({"a": [], "b": ["a"], "c": ["b"]})
        assert detect_cycle(["a", "b", "c"], self._get_fn(tasks)) is None

    def test_diamond_no_cycle(self):
        tasks = self._make_tasks({"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]})
        assert detect_cycle(["a", "b", "c", "d"], self._get_fn(tasks)) is None

    def test_self_cycle(self):
        tasks = self._make_tasks({"a": ["a"]})
        cycle = detect_cycle(["a"], self._get_fn(tasks))
        assert cycle is not None
        assert "a" in cycle

    def test_two_node_cycle(self):
        tasks = self._make_tasks({"a": ["b"], "b": ["a"]})
        cycle = detect_cycle(["a", "b"], self._get_fn(tasks))
        assert cycle is not None
        assert len(cycle) >= 2

    def test_three_node_cycle(self):
        tasks = self._make_tasks({"a": ["c"], "b": ["a"], "c": ["b"]})
        cycle = detect_cycle(["a", "b", "c"], self._get_fn(tasks))
        assert cycle is not None
        # Cycle path repeats the entry node at both ends
        assert cycle[0] == cycle[-1] or len(set(cycle)) < len(cycle)

    def test_empty_returns_none(self):
        assert detect_cycle([], lambda _: None) is None

    def test_cycle_path_forms_valid_cycle(self):
        """Every detected cycle should form a valid back-edge chain."""
        tasks = self._make_tasks({"a": ["b"], "b": ["c"], "c": ["a"]})
        cycle = detect_cycle(["a", "b", "c"], self._get_fn(tasks))
        assert cycle is not None
        # The first and last elements are the same (cycle closes)
        assert cycle[0] == cycle[-1]
        # All elements in the cycle path exist in our task set
        for cid in cycle:
            assert cid in tasks


# ── Property-based tests (hypothesis) ───────────────────────────────
#
# These test the three invariants that must hold for any valid DAG implementation:
#   1. No ready task has an unmet dependency.
#   2. A failed dep never yields a ready dependent.
#   3. Cycles are always detected with their path.


# ── Hypothesis strategies ────────────────────────────────────────────

_STATUSES = ["todo", "in_progress", "done", "failed", "review"]
_COMPLETE = {"done", "review"}
_FAILED = {"failed"}


def _make_fake_task(tid, deps, status):
    class FakeTask:
        pass
    t = FakeTask()
    t.id = tid
    t.depends_on = deps
    t.status = status
    t.title = f"T{tid}"
    return t


@st.composite
def acyclic_graph(draw):
    """Strategy: generate a random DAG (topological order guaranteed).

    Returns (tasks_dict, all_ids) where tasks_dict is {id: FakeTask}
    and edges only go from higher-indexed to lower-indexed nodes.
    """
    n = draw(st.integers(min_value=0, max_value=8))
    ids = [str(i) for i in range(n)]
    statuses = draw(st.lists(st.sampled_from(_STATUSES), min_size=n, max_size=n))
    tasks = {}
    for i, tid in enumerate(ids):
        # Deps can only reference earlier nodes (topological order)
        possible_deps = ids[:i]
        k = draw(st.integers(min_value=0, max_value=min(3, len(possible_deps))))
        deps = draw(st.lists(
            st.sampled_from(possible_deps) if possible_deps else st.nothing(),
            min_size=k,
            max_size=k,
            unique=True,
        )) if possible_deps else []
        tasks[tid] = _make_fake_task(tid, deps, statuses[i])
    return tasks, ids


@st.composite
def cyclic_graph(draw):
    """Strategy: generate a graph with at least one cycle.

    Creates n nodes in a ring: 0→1→2→...→(n-1)→0, where each node's
    depends_on points to the previous one, and the first node depends on
    the last — forming a complete cycle.
    """
    n = draw(st.integers(min_value=2, max_value=6))
    ids = [str(i) for i in range(n)]
    tasks = {tid: _make_fake_task(tid, [], "todo") for tid in ids}
    # Linear chain: ids[i] depends on ids[i-1]
    for i in range(1, n):
        tasks[ids[i]].depends_on = [ids[i - 1]]
    # Back-edge: first node depends on last → closes the ring
    tasks[ids[0]].depends_on = [ids[-1]]
    return tasks, ids


# ── Property 1: No ready task has an unmet dependency ────────────────


@given(acyclic_graph())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_prop_no_ready_task_with_unmet_deps(graph_tuple):
    """Invariant: every task returned as READY has all deps in _COMPLETE."""
    tasks, ids = graph_tuple
    get_fn = lambda id_: tasks.get(id_)
    is_complete = lambda t: t.status in _COMPLETE
    is_failed = lambda t: t.status in _FAILED

    for task in tasks.values():
        status = check_dep_status(
            task.depends_on, get_fn, is_complete, is_failed
        )
        if status == DepStatus.READY:
            for dep_id in task.depends_on:
                dep = get_fn(dep_id)
                assert dep is None or is_complete(dep), (
                    f"Task {task.id} is READY but dep {dep_id} "
                    f"has status {dep.status if dep else 'unknown'}"
                )


# ── Property 2: Failed dep never yields a ready dependent ────────────


@given(acyclic_graph())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_prop_failed_dep_never_ready(graph_tuple):
    """Invariant: if any direct dep is FAILED the task must be BLOCKED."""
    tasks, ids = graph_tuple
    get_fn = lambda id_: tasks.get(id_)
    is_complete = lambda t: t.status in _COMPLETE
    is_failed = lambda t: t.status in _FAILED

    for task in tasks.values():
        has_failed_dep = any(
            (dep := get_fn(d)) is not None and is_failed(dep)
            for d in task.depends_on
        )
        if has_failed_dep:
            status = check_dep_status(
                task.depends_on, get_fn, is_complete, is_failed
            )
            assert status == DepStatus.BLOCKED, (
                f"Task {task.id} has a failed dep but status is {status}"
            )


# ── Property 3: Cycles are always detected with their path ───────────


@given(cyclic_graph())
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
def test_prop_cycles_always_detected(graph_tuple):
    """Invariant: detect_cycle returns a non-empty cycle path for any cyclic graph."""
    tasks, ids = graph_tuple
    get_fn = lambda id_: tasks.get(id_)

    cycle = detect_cycle(ids, get_fn)
    assert cycle is not None, "Expected cycle to be detected"
    assert len(cycle) >= 2, f"Cycle path too short: {cycle}"
    # The cycle path should reference valid task IDs
    for cid in cycle:
        assert cid in tasks, f"Cycle node {cid} not in task set"


@given(acyclic_graph())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_prop_acyclic_graph_no_false_positive(graph_tuple):
    """Invariant: detect_cycle returns None for any acyclic graph."""
    tasks, ids = graph_tuple
    get_fn = lambda id_: tasks.get(id_)

    cycle = detect_cycle(ids, get_fn)
    assert cycle is None, f"False cycle detected in acyclic graph: {cycle}"


# ── DAG validation (cycle detection) via Orchestrator ────────────────


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

    def test_wrap_prompt_working_dir_includes_prebaked_python_env_hint(self):
        """Confined agents must be told the python test toolchain is pre-baked.

        Without this hint agents observe missing/importable deps and rationally
        rebuild a venv + pip install on every run (minutes of per-run waste).
        The hint must (a) name the system interpreter, (b) give the module-form
        commands, and (c) explicitly forbid venv creation / pip installs.
        """
        wrapped = Orchestrator._wrap_prompt(
            "Do something", working_dir="/repo"
        )
        assert "python3 -m pytest" in wrapped
        assert "python3 manage.py test" in wrapped
        assert "do NOT create a virtualenv" in wrapped
        assert "pip install" in wrapped
        # Hint lives in the preamble, ahead of the task prompt body.
        assert wrapped.index("python3 -m pytest") < wrapped.index("Do something")

    def test_wrap_prompt_no_env_hint_without_working_dir(self):
        """No preamble (hence no python hint) when working_dir is absent."""
        wrapped = Orchestrator._wrap_prompt("Do something")
        assert "virtualenv" not in wrapped
        assert "ODIN-STATUS" in wrapped


class TestProofRoundTrip:
    """Per-task proof round-trip: agents write+commit the proof file under
    ``.proof/task-<id>/proof.md`` in their worktree (namespaced so two tasks
    on one spec branch don't collide at merge); the proof MCP comment is a
    short summary+pointer (not a payload); reviewer reads the file directly
    from the read-only worktree mount. The contract lives here in
    ``_wrap_prompt()`` so every worker gets the same instructions.
    """

    def test_wrap_prompt_instructs_agent_to_write_per_task_proof(self):
        """When the proof protocol is active, the prompt must tell the agent
        where to put the canonical proof artifact — namespaced per task
        (``.proof/task-<id>/proof.md``) so two tasks on one spec branch
        don't collide at merge."""
        wrapped = Orchestrator._wrap_prompt(
            "Do something", mcp_task_id="77"
        )
        assert ".proof/task-77/proof.md" in wrapped

    def test_wrap_prompt_does_not_instruct_agent_to_commit_proof(self):
        """The harness auto-commit excludes .proof/ via pathspec and the
        proof upload reads files from disk — agents must NOT commit .proof/
        to git.  Telling them to commit .proof/ is what produced the
        'NNN: proof artifacts' commits that reflection then blamed the
        agent for."""
        wrapped = Orchestrator._wrap_prompt(
            "Do something", mcp_task_id="task-2"
        )
        assert "git add .proof/" not in wrapped
        assert "proof artifacts" not in wrapped

    def test_wrap_prompt_keeps_comment_as_pointer_not_payload(self):
        """The proof MCP comment is a summary + pointer to the per-task
        proof file, not a duplicate of the file's contents. Keeps the
        comment scannable on the board and the disk artifact as the single
        source of truth."""
        wrapped = Orchestrator._wrap_prompt(
            "Do something", mcp_task_id="task-3"
        )
        assert "summary" in wrapped.lower() and "pointer" in wrapped.lower()
        assert ".proof/task-task-3/proof.md" in wrapped

    def test_wrap_prompt_instructs_to_attach_raw_suite_outputs(self):
        """``.proof/`` is the canonical home for raw verify.sh outputs too —
        no cap, no truncation. The comment only summarizes, not replays."""
        wrapped = Orchestrator._wrap_prompt(
            "Do something", mcp_task_id="task-4"
        )
        assert ".proof/" in wrapped
        # Suite outputs go under .proof/ alongside proof.md, not in the comment.
        assert "no cap" in wrapped.lower() or "no truncation" in wrapped.lower() or "complete" in wrapped.lower()

    def test_wrap_prompt_skips_proof_when_skip_proof_true(self):
        """``.proof/`` is for the proof workflow. When a board disables proof
        (board_skip_proof=True), skip_proof flows through and neither the
        file convention nor the comment is required — promote-check
        honours the same flag at the other end."""
        wrapped = Orchestrator._wrap_prompt(
            "Do something", mcp_task_id="task-5", skip_proof=True
        )
        assert ".proof/task-" not in wrapped
        assert ".proof/proof.md" not in wrapped
        # ODIN-STATUS envelope still rides.
        assert "ODIN-STATUS" in wrapped


class TestOrientationBlock:
    """Repo-orientation + efficiency guidance injected by _wrap_prompt.

    Targets two wave-3 wastes: agents re-discovering repo layout every run,
    and mechanical tasks spending 100+ single-tool-call model round-trips.
    """

    def test_efficiency_block_always_present_by_default(self):
        """The batching/efficiency guidance rides on every wrapped prompt."""
        wrapped = Orchestrator._wrap_prompt("Do something")
        assert "Work efficiently" in wrapped
        assert "Batch independent tool calls" in wrapped

    def test_orient_false_omits_block(self):
        """orient=False restores the pre-fix bare prompt (escape hatch)."""
        wrapped = Orchestrator._wrap_prompt("Do something", orient=False)
        assert "Work efficiently" not in wrapped
        assert "Orientation" not in wrapped
        # Core task + envelope still intact.
        assert "Do something" in wrapped
        assert "ODIN-STATUS" in wrapped

    def test_orientation_references_claude_md_when_present(self, tmp_path):
        """CLAUDE.md in the working dir is surfaced so the agent reads it first."""
        (tmp_path / "CLAUDE.md").write_text("# project rules\n")
        wrapped = Orchestrator._wrap_prompt(
            "Do something", working_dir=str(tmp_path)
        )
        assert "## Orientation" in wrapped
        assert "CLAUDE.md" in wrapped

    def test_orientation_references_breadcrumb_index_when_present(self, tmp_path):
        """The breadcrumb index is surfaced only when it actually exists."""
        bc = tmp_path / "docs" / "breadcrumb_analysis"
        bc.mkdir(parents=True)
        (bc / "_INDEX.md").write_text("# breadcrumbs\n")
        wrapped = Orchestrator._wrap_prompt(
            "Do something", working_dir=str(tmp_path)
        )
        assert "breadcrumb_analysis/_INDEX.md" in wrapped

    def test_no_orientation_section_when_docs_absent(self, tmp_path):
        """Never point the agent at docs that aren't there; efficiency stays."""
        wrapped = Orchestrator._wrap_prompt(
            "Do something", working_dir=str(tmp_path)
        )
        assert "## Orientation" not in wrapped
        assert "CLAUDE.md" not in wrapped
        # Universal efficiency guidance is still injected.
        assert "Work efficiently" in wrapped

    def test_orientation_ordered_before_task_and_envelope(self, tmp_path):
        """Orientation must precede the task text so the agent reads it first."""
        (tmp_path / "CLAUDE.md").write_text("# rules\n")
        wrapped = Orchestrator._wrap_prompt(
            "UNIQUE_TASK_MARKER", working_dir=str(tmp_path)
        )
        orient_idx = wrapped.index("## Orientation")
        task_idx = wrapped.index("UNIQUE_TASK_MARKER")
        status_idx = wrapped.index("ODIN-STATUS")
        assert orient_idx < task_idx < status_idx


class TestSelfAuditGate:
    """Pre-completion self-audit gate injected by _wrap_prompt.

    Targets the task-170 failure mode: an agent shipped triplicate helper
    definitions plus ~90 lines of commented-out dead code, which escaped into
    review and cost a full rework round. The gate moves the check LEFT of the
    ODIN-STATUS emission so the agent catches its own slop before declaring
    SUCCESS. The gate rides on every wrapped prompt (like the envelope itself)
    because every SUCCESS emission deserves the same hygiene check.
    """

    def test_gate_section_always_present(self):
        """The self-audit gate rides on every prompt, no MCP or working dir
        required — it guards the ODIN-STATUS emission that every prompt has."""
        wrapped = Orchestrator._wrap_prompt("Do something")
        assert "Pre-completion self-audit gate" in wrapped

    def test_gate_present_with_mcp_and_working_dir(self):
        """Gate survives the full assembly path (preamble + MCP + envelope)."""
        wrapped = Orchestrator._wrap_prompt(
            "Do something", working_dir="/repo", mcp_task_id="task-7"
        )
        assert "Pre-completion self-audit gate" in wrapped
        assert "ODIN-STATUS" in wrapped

    def test_gate_names_both_defect_classes(self):
        """The two cheap defect classes that escape into review: duplicate
        definitions and commented-out dead code. Both must be named so the
        agent knows what to grep for."""
        wrapped = Orchestrator._wrap_prompt("Do something")
        assert "duplicate definition" in wrapped.lower()
        assert "commented-out" in wrapped.lower()

    def test_gate_references_mechanical_assist_script(self):
        """The prompt points at scripts/self_audit_diff.sh so the agent has a
        one-command check rather than re-deriving the grep each run."""
        wrapped = Orchestrator._wrap_prompt("Do something")
        assert "self_audit_diff.sh" in wrapped

    def test_gate_runs_before_odin_status(self):
        """The gate must execute before the SUCCESS emission — that is the whole
        point of moving the check LEFT of review. The heading says so."""
        wrapped = Orchestrator._wrap_prompt("Do something")
        assert "BEFORE ODIN-STATUS" in wrapped

    def test_gate_ordered_after_task_and_before_envelope(self):
        """The gate sits after the task body (the agent has produced its diff)
        and before the ODIN-STATUS envelope (the gate guards that emission).

        Pins the envelope with its full separator — bare 'ODIN-STATUS' also
        appears inside the gate heading ('run BEFORE ODIN-STATUS') and the MCP
        section, so it cannot locate the actual envelope."""
        wrapped = Orchestrator._wrap_prompt("UNIQUE_TASK_MARKER")
        task_idx = wrapped.index("UNIQUE_TASK_MARKER")
        gate_idx = wrapped.index("Pre-completion self-audit gate")
        envelope_idx = wrapped.index("-------ODIN-STATUS-------")
        assert task_idx < gate_idx < envelope_idx

    def test_gate_ordered_before_envelope_with_mcp(self):
        """Ordering holds across the full MCP-laden assembly too."""
        wrapped = Orchestrator._wrap_prompt(
            "UNIQUE_TASK_MARKER", mcp_task_id="task-8"
        )
        mcp_idx = wrapped.index("TaskIt MCP Tools")
        gate_idx = wrapped.index("Pre-completion self-audit gate")
        envelope_idx = wrapped.index("-------ODIN-STATUS-------")
        assert mcp_idx < gate_idx < envelope_idx


class TestAdvisorConsultSection:
    """Optional consult-when-stuck section injected by _wrap_prompt (W7 advisor trial).

    Disabled by default (advisor_enabled=False) — the section only appears
    when the caller opts a task into the trial.
    """

    def test_omitted_by_default(self):
        wrapped = Orchestrator._wrap_prompt("Do something")
        assert "Advisor consult" not in wrapped
        assert "advice_request.md" not in wrapped

    def test_present_when_enabled(self):
        wrapped = Orchestrator._wrap_prompt("Do something", advisor_enabled=True)
        assert "Advisor consult" in wrapped
        assert ".odin/advice_request.md" in wrapped
        assert ".odin/advice.md" in wrapped

    def test_states_the_cap(self):
        wrapped = Orchestrator._wrap_prompt(
            "Do something", advisor_enabled=True, advisor_max_consults=2
        )
        assert "capped at 2" in wrapped

    def test_custom_cap_reflected_in_prompt(self):
        wrapped = Orchestrator._wrap_prompt(
            "Do something", advisor_enabled=True, advisor_max_consults=5
        )
        assert "capped at 5" in wrapped
        assert "5 consults" in wrapped

    def test_instructs_continuing_other_work(self):
        """Core design point: the agent should not block on the answer."""
        wrapped = Orchestrator._wrap_prompt("Do something", advisor_enabled=True)
        assert "do not block on the answer" in wrapped.lower() or "keep working" in wrapped.lower()

    def test_ordered_after_task_and_before_envelope(self):
        wrapped = Orchestrator._wrap_prompt(
            "UNIQUE_TASK_MARKER", advisor_enabled=True
        )
        task_idx = wrapped.index("UNIQUE_TASK_MARKER")
        advisor_idx = wrapped.index("Advisor consult")
        envelope_idx = wrapped.index("-------ODIN-STATUS-------")
        assert task_idx < advisor_idx < envelope_idx


class TestProjectNotesInjection:
    """Durable per-project notes injected by _wrap_prompt after the brief.

    One notes file per project (PROJECT_NOTES.md, path configurable per
    board) so every task starts from the same hard-won context instead of
    re-deriving it. The content is passed in already-capped by
    read_project_notes(); _wrap_prompt only labels and positions it.
    """

    def test_omitted_when_no_notes(self):
        """No notes section when project_notes is empty (the common case)."""
        wrapped = Orchestrator._wrap_prompt("Do something")
        assert "Project Notes" not in wrapped

    def test_present_when_notes_provided(self):
        wrapped = Orchestrator._wrap_prompt(
            "Do something", project_notes="- freshness gate: 24h\n"
        )
        assert "Project Notes" in wrapped
        assert "freshness gate" in wrapped

    def test_section_is_labeled(self):
        wrapped = Orchestrator._wrap_prompt(
            "Do something", project_notes="- a fact\n"
        )
        assert "## Project Notes" in wrapped

    def test_notes_placed_after_brief_before_envelope(self):
        """The task says: after the brief, clearly labeled."""
        wrapped = Orchestrator._wrap_prompt(
            "UNIQUE_TASK_BRIEF", project_notes="UNIQUE_NOTE_FACT"
        )
        brief_idx = wrapped.index("UNIQUE_TASK_BRIEF")
        notes_idx = wrapped.index("UNIQUE_NOTE_FACT")
        envelope_idx = wrapped.index("-------ODIN-STATUS-------")
        assert brief_idx < notes_idx < envelope_idx

    def test_notes_before_mcp_section(self):
        """Notes come right after the brief, ahead of the MCP/proof block."""
        wrapped = Orchestrator._wrap_prompt(
            "UNIQUE_TASK_BRIEF",
            project_notes="UNIQUE_NOTE_FACT",
            mcp_task_id="task-3",
        )
        brief_idx = wrapped.index("UNIQUE_TASK_BRIEF")
        notes_idx = wrapped.index("UNIQUE_NOTE_FACT")
        mcp_idx = wrapped.index("TaskIt MCP Tools")
        assert brief_idx < notes_idx < mcp_idx

    def test_notes_compose_with_working_dir(self):
        wrapped = Orchestrator._wrap_prompt(
            "Do something",
            working_dir="/repo",
            project_notes="- feed quirk: rate limit\n",
        )
        assert "Working directory: /repo" in wrapped
        assert "Project Notes" in wrapped
        assert "feed quirk" in wrapped
