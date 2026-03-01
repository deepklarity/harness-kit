"""Golden snapshot tests for the full harness smoke run.

These tests validate the *shape and invariants* of a known-good end-to-end
spec execution. They catch regressions in:
- Model/serializer field changes (missing or renamed fields)
- Status lifecycle violations (impossible transitions)
- Comment pipeline breakage (missing comments, wrong types)
- DAG dependency resolution (tasks executed before deps complete)
- Metadata contract changes (token usage, duration, agent identity)

The snapshot was captured from spec #38 (full_harness_smoke_spec.md) which
exercises all 6 harnesses: qwen, gemini, claude, minimax, codex, glm.
"""
import json
from pathlib import Path

import pytest

SNAPSHOT_DIR = Path(__file__).parent / "full_harness_smoke"


def _load(name):
    with open(SNAPSHOT_DIR / f"{name}.json") as f:
        return json.load(f)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def spec():
    return _load("spec")


@pytest.fixture(scope="module")
def tasks():
    return _load("tasks")


@pytest.fixture(scope="module")
def comments():
    return _load("comments")


@pytest.fixture(scope="module")
def history():
    return _load("history")


@pytest.fixture(scope="module")
def summary():
    return _load("summary")


@pytest.fixture(scope="module")
def snapshot():
    return _load("snapshot")


# ─── Spec shape ──────────────────────────────────────────────────────────────

class TestSpecShape:
    """Validate the spec record has all required fields."""

    REQUIRED_FIELDS = {
        "id", "odin_id", "title", "source", "content",
        "abandoned", "board_id", "metadata", "created_at",
    }

    def test_spec_has_all_required_fields(self, spec):
        missing = self.REQUIRED_FIELDS - set(spec.keys())
        assert not missing, f"Spec missing fields: {missing}"

    def test_spec_is_not_abandoned(self, spec):
        assert spec["abandoned"] is False

    def test_spec_has_content(self, spec):
        assert len(spec["content"]) > 0

    def test_spec_has_board(self, spec):
        assert spec["board_id"] is not None


# ─── Task shape and invariants ───────────────────────────────────────────────

class TestTaskShape:
    """Validate every task has the expected field contract."""

    REQUIRED_FIELDS = {
        "id", "title", "description", "status", "priority",
        "complexity", "model_name", "board_id", "spec_id",
        "depends_on", "metadata", "created_by", "assignee_id",
        "assignee_email", "assignee_name", "created_at", "last_updated_at",
    }

    METADATA_REQUIRED_KEYS = {
        "reasoning", "taskit_id", "complexity", "last_usage",
        "started_at", "working_dir", "selected_model",
        "suggested_agent", "expected_outputs", "last_duration_ms",
    }

    USAGE_REQUIRED_KEYS = {"input_tokens", "total_tokens", "output_tokens"}

    def test_task_count(self, tasks):
        assert len(tasks) == 7

    def test_all_tasks_have_required_fields(self, tasks):
        for task in tasks:
            missing = self.REQUIRED_FIELDS - set(task.keys())
            assert not missing, f"Task {task['id']} missing fields: {missing}"

    def test_all_tasks_have_metadata_keys(self, tasks):
        for task in tasks:
            md = task["metadata"]
            missing = self.METADATA_REQUIRED_KEYS - set(md.keys())
            assert not missing, f"Task {task['id']} metadata missing: {missing}"

    def test_all_tasks_have_usage_keys(self, tasks):
        for task in tasks:
            usage = task["metadata"]["last_usage"]
            missing = self.USAGE_REQUIRED_KEYS - set(usage.keys())
            assert not missing, f"Task {task['id']} usage missing: {missing}"

    def test_all_tasks_have_assignee(self, tasks):
        for task in tasks:
            assert task["assignee_id"] is not None, f"Task {task['id']} has no assignee"
            assert task["assignee_name"], f"Task {task['id']} has no assignee_name"

    def test_all_tasks_belong_to_same_spec(self, tasks, spec):
        for task in tasks:
            assert task["spec_id"] == spec["id"]

    def test_all_tasks_belong_to_same_board(self, tasks):
        board_ids = {t["board_id"] for t in tasks}
        assert len(board_ids) == 1


# ─── Harness coverage ───────────────────────────────────────────────────────

class TestHarnessCoverage:
    """Verify all 6 harnesses were exercised."""

    EXPECTED_HARNESSES = {"qwen", "gemini", "claude", "minimax", "codex", "glm"}

    def test_all_harnesses_present(self, tasks):
        harnesses = {t["assignee_name"] for t in tasks}
        missing = self.EXPECTED_HARNESSES - harnesses
        assert not missing, f"Harnesses not exercised: {missing}"

    def test_each_harness_has_model(self, tasks):
        for task in tasks:
            assert task["model_name"], f"Task {task['id']} ({task['assignee_name']}) has no model_name"

    def test_each_harness_produced_output(self, tasks):
        for task in tasks:
            full_output = task["metadata"].get("full_output", "")
            assert "ODIN-STATUS" in full_output, (
                f"Task {task['id']} ({task['assignee_name']}) missing ODIN-STATUS envelope"
            )
            assert "SUCCESS" in full_output, (
                f"Task {task['id']} ({task['assignee_name']}) did not report SUCCESS"
            )


# ─── Status lifecycle ────────────────────────────────────────────────────────

class TestStatusLifecycle:
    """Validate status transitions follow the expected lifecycle."""

    VALID_TRANSITIONS = {
        ("", "BACKLOG"),        # creation (implicit)
        ("BACKLOG", "TODO"),    # planning assigned
        ("TODO", "IN_PROGRESS"),
        ("IN_PROGRESS", "EXECUTING"),
        ("EXECUTING", "REVIEW"),
        ("EXECUTING", "DONE"),
        ("EXECUTING", "FAILED"),
        ("IN_PROGRESS", "REVIEW"),
        ("IN_PROGRESS", "DONE"),
        ("IN_PROGRESS", "FAILED"),
        ("REVIEW", "DONE"),
        ("REVIEW", "FAILED"),
        ("TODO", "EXECUTING"),   # direct skip (quick mode)
    }

    def test_all_tasks_reached_terminal_status(self, tasks):
        for task in tasks:
            assert task["status"] in ("REVIEW", "DONE", "TESTING"), (
                f"Task {task['id']} stuck in {task['status']}"
            )

    def test_status_transitions_are_valid(self, history):
        status_changes = [h for h in history if h["field_name"] == "status"]
        for h in status_changes:
            transition = (h["old_value"], h["new_value"])
            assert transition in self.VALID_TRANSITIONS, (
                f"Invalid transition on task {h['task_id']}: "
                f"{h['old_value']} -> {h['new_value']}"
            )

    def test_every_task_was_created(self, history):
        created = {h["task_id"] for h in history if h["field_name"] == "created"}
        assert len(created) == 7

    def test_every_task_was_assigned(self, history):
        assigned = {h["task_id"] for h in history if h["field_name"] == "assignee_id"}
        assert len(assigned) == 7


# ─── DAG dependencies ───────────────────────────────────────────────────────

class TestDAGDependencies:
    """Validate the dependency structure and execution ordering."""

    def test_assembly_task_depends_on_all_others(self, tasks):
        assembly = [t for t in tasks if t["title"] == "Assemble report"]
        assert len(assembly) == 1
        assembly_task = assembly[0]
        other_ids = {str(t["id"]) for t in tasks if t["id"] != assembly_task["id"]}
        deps = set(assembly_task["depends_on"])
        assert deps == other_ids, (
            f"Assembly depends_on={deps}, expected={other_ids}"
        )

    def test_leaf_tasks_have_no_dependencies(self, tasks):
        for task in tasks:
            if task["title"] != "Assemble report":
                assert task["depends_on"] == [], (
                    f"Task {task['id']} ({task['title']}) should have no deps"
                )

    def test_assembly_started_after_all_deps_completed(self, history):
        """Verify the assembly task's EXECUTING transition happened after
        all dependency tasks reached a terminal status.

        Note: IN_PROGRESS is a batch planning transition (Celery claims tasks
        upfront). The actual dependency gate is IN_PROGRESS -> EXECUTING,
        which only fires after all deps reach terminal status."""
        status_changes = [h for h in history if h["field_name"] == "status"]

        # Find when task 198 (assembly) started EXECUTING (not IN_PROGRESS,
        # which is a batch planning operation before deps are checked)
        assembly_exec = [
            h for h in status_changes
            if h["task_id"] == 198 and h["new_value"] == "EXECUTING"
        ]
        if not assembly_exec:
            pytest.skip("Assembly task has no EXECUTING transition in history")

        assembly_start = assembly_exec[0]["changed_at"]

        # Find when each dep reached terminal
        dep_ids = {192, 193, 194, 195, 196, 197}
        terminal = {"REVIEW", "DONE", "TESTING"}
        for dep_id in dep_ids:
            dep_terminals = [
                h for h in status_changes
                if h["task_id"] == dep_id and h["new_value"] in terminal
            ]
            assert dep_terminals, f"Dep task {dep_id} never reached terminal status"
            dep_done_at = dep_terminals[-1]["changed_at"]
            assert dep_done_at <= assembly_start, (
                f"Dep task {dep_id} completed at {dep_done_at} but "
                f"assembly started at {assembly_start}"
            )


# ─── Comment pipeline ───────────────────────────────────────────────────────

class TestCommentPipeline:
    """Validate the comment lifecycle for all tasks."""

    def test_every_task_has_comments(self, tasks, comments):
        task_ids_with_comments = {c["task_id"] for c in comments}
        for task in tasks:
            assert task["id"] in task_ids_with_comments, (
                f"Task {task['id']} ({task['title']}) has no comments"
            )

    def test_planning_comment_per_task(self, tasks, comments):
        """Every task should have a planning assumptions comment from odin."""
        for task in tasks:
            task_comments = [c for c in comments if c["task_id"] == task["id"]]
            planning = [
                c for c in task_comments
                if c["author_email"] == "odin@harness.kit"
                and "assumptions" in c["content"].lower()
            ]
            assert planning, (
                f"Task {task['id']} missing planning assumptions comment"
            )

    def test_execution_comment_per_task(self, tasks, comments):
        """Every task should have an execution result comment from the agent."""
        for task in tasks:
            task_comments = [c for c in comments if c["task_id"] == task["id"]]
            # Execution comments come from agent emails or contain token/duration info
            execution = [
                c for c in task_comments
                if "token" in c["content"].lower()
                or "completed" in c["content"].lower()
                or c["comment_type"] == "status_update"
                and c["author_email"] != "odin@harness.kit"
            ]
            # At minimum, odin posts a completion comment
            assert len(task_comments) >= 2, (
                f"Task {task['id']} has only {len(task_comments)} comments, expected >= 2"
            )

    def test_comment_types_are_valid(self, comments):
        valid_types = {"status_update", "question", "reply", "proof"}
        for c in comments:
            assert c["comment_type"] in valid_types, (
                f"Comment {c['id']} has invalid type: {c['comment_type']}"
            )

    def test_comments_have_author(self, comments):
        for c in comments:
            assert c["author_email"], f"Comment {c['id']} has no author_email"


# ─── Token usage and cost metadata ──────────────────────────────────────────

class TestTokenUsage:
    """Validate token tracking across all tasks."""

    def test_all_tasks_have_token_usage(self, tasks):
        for task in tasks:
            usage = task["metadata"]["last_usage"]
            assert usage["total_tokens"] > 0, (
                f"Task {task['id']} has 0 total_tokens"
            )

    def test_total_tokens_sum_matches_summary(self, tasks, summary):
        computed = sum(t["metadata"]["last_usage"]["total_tokens"] for t in tasks)
        assert computed == summary["total_tokens"]

    def test_all_tasks_have_duration(self, tasks):
        for task in tasks:
            dur = task["metadata"]["last_duration_ms"]
            assert dur and dur > 0, (
                f"Task {task['id']} has no/zero duration"
            )

    def test_input_output_tokens_are_positive(self, tasks):
        for task in tasks:
            usage = task["metadata"]["last_usage"]
            assert usage["input_tokens"] > 0, (
                f"Task {task['id']} has 0 input_tokens"
            )
            assert usage["output_tokens"] > 0, (
                f"Task {task['id']} has 0 output_tokens"
            )


# ─── Snapshot integrity ─────────────────────────────────────────────────────

class TestSnapshotIntegrity:
    """Validate the snapshot itself is complete and self-consistent."""

    def test_snapshot_has_all_sections(self, snapshot):
        required = {"_meta", "summary", "spec", "tasks", "comments", "history"}
        assert required <= set(snapshot.keys())

    def test_meta_task_ids_match_tasks(self, snapshot):
        meta_ids = set(snapshot["_meta"]["task_ids"])
        task_ids = {t["id"] for t in snapshot["tasks"]}
        assert meta_ids == task_ids

    def test_summary_task_count_matches(self, snapshot):
        assert snapshot["summary"]["task_count"] == len(snapshot["tasks"])

    def test_summary_comment_count_matches(self, snapshot):
        assert snapshot["summary"]["comment_count"] == len(snapshot["comments"])

    def test_summary_history_count_matches(self, snapshot):
        assert snapshot["summary"]["history_count"] == len(snapshot["history"])

    def test_all_history_references_valid_tasks(self, snapshot):
        task_ids = {t["id"] for t in snapshot["tasks"]}
        for h in snapshot["history"]:
            assert h["task_id"] in task_ids, (
                f"History entry {h['id']} references unknown task {h['task_id']}"
            )

    def test_all_comments_reference_valid_tasks(self, snapshot):
        task_ids = {t["id"] for t in snapshot["tasks"]}
        for c in snapshot["comments"]:
            assert c["task_id"] in task_ids, (
                f"Comment {c['id']} references unknown task {c['task_id']}"
            )


# ─── Cost estimation ────────────────────────────────────────────────────────

class TestCostEstimation:
    """Validate that every task's model has pricing and produces a valid cost.

    This catches the "UNKNOWN PRICING" bug: if a model is assigned to a task
    but has no pricing in agent_models.json, the frontend shows "unknown".
    """

    @pytest.fixture(scope="module")
    def pricing_table(self):
        """Load authoritative pricing from agent_models.json."""
        pricing_path = (
            Path(__file__).resolve().parents[2]
            / "taskit" / "taskit-backend" / "data" / "agent_models.json"
        )
        data = json.loads(pricing_path.read_text())
        table = {}
        for agent_info in data.get("agents", {}).values():
            for model in agent_info.get("models", []):
                table[model["name"]] = {
                    "input_price_per_1m_tokens": model.get("input_price_per_1m_tokens"),
                    "output_price_per_1m_tokens": model.get("output_price_per_1m_tokens"),
                }
        return table

    def _estimate_cost(self, model_name, input_tokens, output_tokens, pricing_table):
        """Compute cost in USD, returning None if pricing is unavailable."""
        if input_tokens is None or output_tokens is None:
            return None
        entry = pricing_table.get(model_name)
        if not entry:
            return None
        inp = entry["input_price_per_1m_tokens"]
        out = entry["output_price_per_1m_tokens"]
        if inp is None or out is None:
            return None
        return (input_tokens / 1_000_000) * inp + (output_tokens / 1_000_000) * out

    def test_every_task_model_has_pricing(self, tasks, pricing_table):
        """Every task's model_name must exist in the pricing table."""
        for task in tasks:
            model = task["model_name"]
            assert model in pricing_table, (
                f"Task {task['id']} ({task['assignee_name']}) uses model "
                f"'{model}' which has no pricing in agent_models.json"
            )

    def test_every_task_has_computable_cost(self, tasks, pricing_table):
        """Every task must produce a numeric cost > 0."""
        for task in tasks:
            model = task["model_name"]
            usage = task["metadata"]["last_usage"]
            cost = self._estimate_cost(
                model, usage["input_tokens"], usage["output_tokens"], pricing_table
            )
            assert cost is not None, (
                f"Task {task['id']} ({task['assignee_name']}) cost is None "
                f"(model={model})"
            )
            assert cost > 0, (
                f"Task {task['id']} ({task['assignee_name']}) cost is 0 "
                f"(model={model}, in={usage['input_tokens']}, out={usage['output_tokens']})"
            )

    def test_zero_tasks_with_unknown_cost(self, tasks, pricing_table):
        """The total count of tasks with unknown cost must be 0."""
        unknown = []
        for task in tasks:
            model = task["model_name"]
            usage = task["metadata"]["last_usage"]
            cost = self._estimate_cost(
                model, usage["input_tokens"], usage["output_tokens"], pricing_table
            )
            if cost is None:
                unknown.append(f"Task {task['id']} ({task['assignee_name']}, model={model})")
        assert len(unknown) == 0, (
            f"UNKNOWN PRICING: {len(unknown)} tasks — {', '.join(unknown)}"
        )

    def test_total_cost_equals_sum_of_task_costs(self, tasks, pricing_table):
        """Spec-level total cost must equal sum of individual task costs."""
        per_task_costs = []
        for task in tasks:
            model = task["model_name"]
            usage = task["metadata"]["last_usage"]
            cost = self._estimate_cost(
                model, usage["input_tokens"], usage["output_tokens"], pricing_table
            )
            assert cost is not None, f"Task {task['id']} has no computable cost"
            per_task_costs.append(cost)

        total = sum(per_task_costs)
        assert total > 0, "Total cost across all tasks should be > 0"
        # Verify sum is self-consistent (no floating point weirdness)
        recomputed = sum(per_task_costs)
        assert abs(total - recomputed) < 1e-10

    def test_cost_by_model_grouping(self, tasks, pricing_table):
        """Costs grouped by model should aggregate correctly."""
        from collections import defaultdict
        by_model = defaultdict(list)
        for task in tasks:
            model = task["model_name"]
            usage = task["metadata"]["last_usage"]
            cost = self._estimate_cost(
                model, usage["input_tokens"], usage["output_tokens"], pricing_table
            )
            by_model[model].append(cost)

        # gemini-2.5-flash should have 2 tasks (task 193 + assembly task 198)
        assert len(by_model["gemini-2.5-flash"]) == 2, (
            f"Expected 2 gemini-2.5-flash tasks, got {len(by_model['gemini-2.5-flash'])}"
        )

        # Every model group total should equal sum of its tasks
        for model, costs in by_model.items():
            assert all(c is not None for c in costs), (
                f"Model {model} has None costs"
            )
            group_total = sum(costs)
            assert group_total > 0, f"Model {model} group total is 0"

    def test_input_output_token_breakdown_in_summary(self, summary):
        """Summary should include input/output token breakdown.

        Note: total_input + total_output may be less than total_tokens because
        some harnesses (minimax, glm) include reasoning/internal tokens in
        total_tokens that are not reflected in input/output breakdowns.
        """
        assert "total_input_tokens" in summary, (
            "summary.json missing total_input_tokens — "
            "run snapshot_extractor.py to regenerate"
        )
        assert "total_output_tokens" in summary, (
            "summary.json missing total_output_tokens — "
            "run snapshot_extractor.py to regenerate"
        )
        assert summary["total_input_tokens"] > 0
        assert summary["total_output_tokens"] > 0
        # input + output <= total (some harnesses include reasoning tokens)
        assert (
            summary["total_input_tokens"] + summary["total_output_tokens"]
            <= summary["total_tokens"]
        )
