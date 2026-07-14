"""Tests for cost estimation in TaskIt backend.

Covers: pricing utility, task cost estimation (base + detail serializers),
spec cost summary (detail + diagnostic), pricing endpoint.

Cost is now computed on-the-fly from trace comments (source of truth),
not from cached metadata.last_usage.
"""

import json

from .base import APITestCase
from tasks.models import TaskComment


def _clear_pricing_cache():
    """Clear the LRU cache so tests pick up current agent_models.json."""
    from tasks.pricing import get_pricing_table
    get_pricing_table.cache_clear()


ORCHESTRATOR_TRACE_LIMIT = 50000  # Matches odin/orchestrator.py PAYLOAD_RAW_OUTPUT_LIMIT


def _make_trace_comment(task, input_tokens, output_tokens, total_tokens=None, fmt="claude"):
    """Create a trace comment simulating what the orchestrator posts.

    Args:
        task: Task instance.
        input_tokens: int.
        output_tokens: int.
        total_tokens: int (defaults to input + output).
        fmt: "claude" (modelUsage), "gemini" (result stats), or "minimax" (step_finish).
    """
    total = total_tokens or (input_tokens + output_tokens)
    if fmt == "claude":
        content = "\n".join([
            '{"type":"text","part":{"text":"done"}}',
            json.dumps({"modelUsage": {"model": {
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
            }}}),
        ])
    elif fmt == "gemini":
        content = json.dumps({
            "type": "result",
            "result": "done",
            "stats": {"total_tokens": total, "input_tokens": input_tokens, "output_tokens": output_tokens},
        })
    elif fmt == "minimax":
        content = "\n".join([
            '{"type":"text","text":"done"}',
            json.dumps({"type": "step_finish", "part": {"tokens": {
                "total": total, "input": input_tokens, "output": output_tokens,
            }}}),
        ])
    else:
        raise ValueError(f"Unknown format: {fmt}")

    TaskComment.objects.create(
        task=task,
        author_email="odin@system",
        content=content,
        attachments=["trace:execution_jsonl"],
    )


def _truncate_trace_like_orchestrator(raw: str, limit: int = ORCHESTRATOR_TRACE_LIMIT) -> str:
    """Replicate the orchestrator's trace truncation logic.

    Keeps the first (limit - tail_preserve) chars and the last tail_preserve
    chars, joined on newline boundaries. This preserves the modelUsage summary
    that Claude Code puts at the very end.
    """
    if len(raw) <= limit:
        return raw
    tail_preserve = 2000  # Matches TRACE_TAIL_PRESERVE in orchestrator
    head_budget = limit - tail_preserve
    head_end = raw.rfind("\n", 0, head_budget)
    if head_end == -1:
        head_end = head_budget
    tail_start = raw.rfind("\n", len(raw) - tail_preserve)
    if tail_start == -1:
        tail_start = len(raw) - tail_preserve
    else:
        tail_start += 1
    return raw[:head_end] + "\n" + raw[tail_start:]


def _make_long_claude_trace_comment(task, input_tokens, output_tokens, total_chars=60000):
    """Create a realistic long Claude Code trace that exceeds the orchestrator limit.

    Claude Code JSONL has many text events (tool calls, thinking, etc.) with
    the modelUsage summary as the VERY LAST line. The orchestrator truncates
    traces that exceed the limit. This helper simulates that production scenario.
    """
    model_usage_line = json.dumps({"modelUsage": {"claude-sonnet-5": {
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheReadInputTokens": 0,
        "cacheCreationInputTokens": 0,
    }}})

    # Build text events to fill the trace
    lines = []
    filler_text = "x" * 200  # Each text event ~250 chars as JSON
    while len("\n".join(lines)) < total_chars:
        lines.append(json.dumps({"type": "text", "part": {"text": filler_text}}))
    # Append modelUsage as the last line (this is where Claude Code puts it)
    lines.append(model_usage_line)

    full_trace = "\n".join(lines)
    # Simulate orchestrator truncation (smart: preserves tail)
    truncated = _truncate_trace_like_orchestrator(full_trace)

    TaskComment.objects.create(
        task=task,
        author_email="odin@system",
        content=truncated,
        attachments=["trace:execution_jsonl"],
    )


class TestPricingUtility(APITestCase):
    """Test the pricing module functions."""

    def setUp(self):
        super().setUp()
        _clear_pricing_cache()

    def test_get_pricing_table_returns_dict(self):
        from tasks.pricing import get_pricing_table
        table = get_pricing_table()
        self.assertIsInstance(table, dict)
        self.assertIn("claude-sonnet-5", table)

    def test_pricing_table_known_model(self):
        from tasks.pricing import get_pricing_table
        table = get_pricing_table()
        entry = table["claude-sonnet-5"]
        self.assertEqual(entry["input_price_per_1m_tokens"], 3.00)
        self.assertEqual(entry["output_price_per_1m_tokens"], 15.00)

    def test_pricing_table_nonexistent_model(self):
        from tasks.pricing import get_pricing_table
        table = get_pricing_table()
        self.assertNotIn("totally-fake-model-xyz", table)

    def test_estimate_task_cost_known(self):
        from tasks.pricing import estimate_task_cost
        cost = estimate_task_cost("claude-sonnet-5", 1000, 500)
        # (1000/1M)*3.00 + (500/1M)*15.00 = 0.003 + 0.0075 = 0.0105
        self.assertAlmostEqual(cost, 0.0105, places=6)

    def test_estimate_task_cost_unknown_model(self):
        from tasks.pricing import estimate_task_cost
        cost = estimate_task_cost("totally-fake-model-xyz", 1000, 500)
        self.assertIsNone(cost)

    def test_estimate_task_cost_null_tokens(self):
        from tasks.pricing import estimate_task_cost
        cost = estimate_task_cost("claude-sonnet-5", None, None)
        self.assertIsNone(cost)

    def test_estimate_task_cost_nonexistent_model(self):
        from tasks.pricing import estimate_task_cost
        cost = estimate_task_cost("nonexistent-model", 1000, 500)
        self.assertIsNone(cost)


class TestTaskDetailCostEstimation(APITestCase):
    """Test that task detail endpoint includes estimated cost (from trace comments)."""

    def test_task_detail_includes_estimated_cost(self):
        board = self.make_board()
        task = self.make_task(board, model_name="claude-sonnet-5")
        _make_trace_comment(task, input_tokens=10000, output_tokens=5000)
        resp = self.client.get(f"/tasks/{task.id}/detail/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("estimated_cost_usd", resp.data)
        # (10000/1M)*3.00 + (5000/1M)*15.00 = 0.03 + 0.075 = 0.105
        self.assertAlmostEqual(resp.data["estimated_cost_usd"], 0.105, places=4)

    def test_task_detail_null_usage_returns_null_cost(self):
        board = self.make_board()
        task = self.make_task(board, model_name="claude-sonnet-5")
        # No trace comment → no usage → null cost
        resp = self.client.get(f"/tasks/{task.id}/detail/")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.data["estimated_cost_usd"])

    def test_task_detail_unknown_model_returns_null_cost(self):
        board = self.make_board()
        task = self.make_task(board, model_name="totally-fake-model-xyz")
        _make_trace_comment(task, input_tokens=10000, output_tokens=5000)
        resp = self.client.get(f"/tasks/{task.id}/detail/")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.data["estimated_cost_usd"])

    def test_task_detail_includes_usage_field(self):
        """The new top-level 'usage' field is populated from trace comment."""
        board = self.make_board()
        task = self.make_task(board, model_name="claude-sonnet-5")
        _make_trace_comment(task, input_tokens=10000, output_tokens=5000)
        resp = self.client.get(f"/tasks/{task.id}/detail/")
        self.assertIsNotNone(resp.data["usage"])
        self.assertEqual(resp.data["usage"]["input_tokens"], 10000)
        self.assertEqual(resp.data["usage"]["output_tokens"], 5000)

    def test_task_detail_no_trace_has_null_usage(self):
        """No trace comment → usage field is null."""
        board = self.make_board()
        task = self.make_task(board)
        resp = self.client.get(f"/tasks/{task.id}/detail/")
        self.assertIsNone(resp.data["usage"])

    def test_long_claude_trace_preserves_cost(self):
        """Claude traces >50K chars must still produce valid cost.

        Claude Code puts modelUsage as the LAST line. The orchestrator
        truncates to 50K chars from the front, which chops off the end.
        This is the exact production scenario where cost shows 'unknown'.
        """
        board = self.make_board()
        task = self.make_task(board, model_name="claude-sonnet-5")
        _make_long_claude_trace_comment(task, input_tokens=10000, output_tokens=5000)

        resp = self.client.get(f"/tasks/{task.id}/detail/")
        self.assertEqual(resp.status_code, 200)

        # Usage must be extracted even from a truncated trace
        usage = resp.data.get("usage")
        self.assertIsNotNone(usage, "usage is None — modelUsage lost to trace truncation")
        self.assertEqual(usage["input_tokens"], 10000)
        self.assertEqual(usage["output_tokens"], 5000)

        # Cost must be computable
        self.assertIsNotNone(
            resp.data["estimated_cost_usd"],
            "cost is None — trace truncation killed cost estimation"
        )
        # (10000/1M)*3.00 + (5000/1M)*15.00 = 0.03 + 0.075 = 0.105
        self.assertAlmostEqual(resp.data["estimated_cost_usd"], 0.105, places=4)


class TestBaseTaskSerializerCost(APITestCase):
    """Test that the base TaskSerializer includes estimated_cost_usd.

    This is critical: spec detail and board detail responses use TaskSerializer,
    so every task list includes per-task cost without extra API calls.
    """

    def test_task_list_includes_estimated_cost(self):
        """Tasks in board detail should include estimated_cost_usd."""
        board = self.make_board()
        task = self.make_task(board, model_name="claude-sonnet-5")
        _make_trace_comment(task, input_tokens=10000, output_tokens=5000, total_tokens=15000)
        resp = self.client.get(f"/boards/{board.id}/")
        self.assertEqual(resp.status_code, 200)
        tasks = resp.data["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertIn("estimated_cost_usd", tasks[0])
        self.assertAlmostEqual(tasks[0]["estimated_cost_usd"], 0.105, places=4)

    def test_spec_detail_tasks_include_estimated_cost(self):
        """Tasks nested in spec detail should include estimated_cost_usd."""
        board = self.make_board()
        spec = self.make_spec(board)
        task = self.make_task(board, spec=spec, model_name="minimax-coding-plan/MiniMax-M3")
        _make_trace_comment(task, input_tokens=5000, output_tokens=2000, fmt="gemini")
        resp = self.client.get(f"/specs/{spec.id}/")
        self.assertEqual(resp.status_code, 200)
        tasks = resp.data["tasks"]
        self.assertEqual(len(tasks), 1)
        # (5000/1M)*0.30 + (2000/1M)*1.20 = 0.0015 + 0.0024 = 0.0039
        self.assertAlmostEqual(tasks[0]["estimated_cost_usd"], 0.0039, places=4)

    def test_task_no_usage_returns_null_cost(self):
        """Task with no trace comment should have null cost."""
        board = self.make_board()
        self.make_task(board)
        resp = self.client.get(f"/boards/{board.id}/")
        tasks = resp.data["tasks"]
        self.assertIsNone(tasks[0]["estimated_cost_usd"])


class TestSpecCostSummary(APITestCase):
    """Test spec-level cost aggregation (both detail and diagnostic endpoints)."""

    def test_spec_detail_includes_cost_summary(self):
        """The regular spec detail endpoint (not just diagnostic) must include cost_summary."""
        board = self.make_board()
        spec = self.make_spec(board)
        task = self.make_task(
            board, title="Task 1", spec=spec, model_name="claude-sonnet-5",
        )
        _make_trace_comment(task, input_tokens=10000, output_tokens=5000, total_tokens=15000)
        resp = self.client.get(f"/specs/{spec.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("cost_summary", resp.data)
        summary = resp.data["cost_summary"]
        self.assertAlmostEqual(summary["total_cost_usd"], 0.105, places=4)
        self.assertEqual(summary["total_input_tokens"], 10000)
        self.assertEqual(summary["total_output_tokens"], 5000)

    def test_spec_detail_cost_summary_token_breakdown(self):
        """cost_summary must include total_input_tokens and total_output_tokens."""
        board = self.make_board()
        spec = self.make_spec(board)
        t1 = self.make_task(board, title="T1", spec=spec, model_name="claude-sonnet-5")
        _make_trace_comment(t1, input_tokens=8000, output_tokens=2000, total_tokens=10000)
        t2 = self.make_task(board, title="T2", spec=spec, model_name="minimax-coding-plan/MiniMax-M3")
        _make_trace_comment(t2, input_tokens=3000, output_tokens=1000, total_tokens=4000, fmt="gemini")
        resp = self.client.get(f"/specs/{spec.id}/")
        summary = resp.data["cost_summary"]
        self.assertEqual(summary["total_input_tokens"], 11000)
        self.assertEqual(summary["total_output_tokens"], 3000)
        self.assertEqual(summary["total_tokens"], 14000)

    def test_spec_diagnostic_includes_cost_summary(self):
        board = self.make_board()
        spec = self.make_spec(board)
        # Task with known pricing and usage (claude)
        t1 = self.make_task(board, title="Task 1", spec=spec, model_name="claude-sonnet-5")
        _make_trace_comment(t1, input_tokens=10000, output_tokens=5000, total_tokens=15000)
        # Task with known pricing and usage (minimax)
        t2 = self.make_task(board, title="Task 2", spec=spec, model_name="minimax-coding-plan/MiniMax-M3")
        _make_trace_comment(t2, input_tokens=5000, output_tokens=2000, total_tokens=7000, fmt="gemini")
        # Task with truly unknown model pricing
        t3 = self.make_task(board, title="Task 3", spec=spec, model_name="totally-fake-model-xyz")
        _make_trace_comment(t3, input_tokens=1000, output_tokens=500, total_tokens=1500)
        # Task with no usage
        self.make_task(board, title="Task 4", spec=spec)

        resp = self.client.get(f"/specs/{spec.id}/diagnostic/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("cost_summary", resp.data)

        summary = resp.data["cost_summary"]
        # claude:  (10000/1M)*3.00 + (5000/1M)*15.00 = 0.105
        # minimax: (5000/1M)*0.30  + (2000/1M)*1.20  = 0.0039
        # total = 0.1089
        self.assertAlmostEqual(summary["total_cost_usd"], 0.1089, places=4)
        self.assertEqual(summary["total_tokens"], 23500)  # 15000 + 7000 + 1500
        self.assertEqual(summary["tasks_with_unknown_cost"], 1)  # fake model; no-usage task not counted
        self.assertIn("claude-sonnet-5", summary["cost_by_model"])
        self.assertIn("minimax-coding-plan/MiniMax-M3", summary["cost_by_model"])

    def test_spec_diagnostic_no_tasks(self):
        board = self.make_board()
        spec = self.make_spec(board)

        resp = self.client.get(f"/specs/{spec.id}/diagnostic/")
        self.assertEqual(resp.status_code, 200)
        summary = resp.data["cost_summary"]
        self.assertEqual(summary["total_cost_usd"], 0)
        self.assertEqual(summary["total_tokens"], 0)
        self.assertEqual(summary["tasks_with_unknown_cost"], 0)


class TestAllHarnessModelPricing(APITestCase):
    """Verify EVERY active model in agent_models.json has valid pricing.

    This is the canary test: if any active model has null pricing, the UI
    shows "unknown" cost which is exactly the bug we're preventing.

    Expectations are derived from agent_models.json via the pricing module's
    single-source helpers (get_pricing_table, _agent_registry) — never pinned
    to retired model names. F45 added get_active_agents/get_agent_default_model
    so retired providers (qwen #102, gemini F12) stay out of test fixtures;
    retired names belong only in negative-case fixtures, never here.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Snapshot the active lineup from agent_models.json — the single
        # source of truth. The pricing table is loaded from the same file,
        # so this list is the contract we verify the pricing table honors.
        from tasks.pricing import _agent_registry
        cls._active_agents = _agent_registry()
        cls._active_models = [
            (agent_name, model["name"])
            for agent_name, info in cls._active_agents.items()
            for model in info.get("models", [])
            if model.get("name")
        ]

    def setUp(self):
        super().setUp()
        _clear_pricing_cache()

    def _pricing_table(self):
        from tasks.pricing import get_pricing_table
        return get_pricing_table()

    def test_every_active_agent_has_models(self):
        """Sanity check: each active agent in agent_models.json declares models.

        Empty agent entries would silently route to nothing — surfacing them
        here keeps the contract visible.
        """
        self.assertGreater(len(self._active_agents), 0)
        for name, info in self._active_agents.items():
            models = info.get("models") or []
            self.assertGreater(
                len(models), 0,
                f"Agent '{name}' in agent_models.json has no models",
            )

    def test_all_active_models_present_in_pricing_table(self):
        """Every active model in agent_models.json must appear in the pricing table."""
        table = self._pricing_table()
        for agent_name, model_name in self._active_models:
            self.assertIn(
                model_name, table,
                f"Model '{model_name}' (agent '{agent_name}') missing from pricing table",
            )

    def test_no_active_model_has_null_pricing(self):
        """No active model should have null input or output pricing."""
        table = self._pricing_table()
        for agent_name, model_name in self._active_models:
            entry = table[model_name]
            self.assertIsNotNone(
                entry["input_price_per_1m_tokens"],
                f"{model_name} (agent {agent_name}) has null input pricing",
            )
            self.assertIsNotNone(
                entry["output_price_per_1m_tokens"],
                f"{model_name} (agent {agent_name}) has null output pricing",
            )

    def test_pricing_table_matches_agent_models_json(self):
        """Pricing values must round-trip from agent_models.json unchanged.

        The pricing table is loaded from agent_models.json, so this verifies
        the loader preserves all pricing fields. Catches bugs like dropped
        cache_read_price_per_1m_tokens.
        """
        table = self._pricing_table()
        for agent_name, info in self._active_agents.items():
            for model in info.get("models", []):
                name = model["name"]
                self.assertIn(name, table)
                entry = table[name]
                self.assertAlmostEqual(
                    entry["input_price_per_1m_tokens"],
                    model["input_price_per_1m_tokens"], places=6,
                    msg=f"{name} input price not preserved",
                )
                self.assertAlmostEqual(
                    entry["output_price_per_1m_tokens"],
                    model["output_price_per_1m_tokens"], places=6,
                    msg=f"{name} output price not preserved",
                )

    def test_all_active_models_produce_numeric_cost(self):
        """estimate_task_cost should return a float (not None) for every active model."""
        from tasks.pricing import estimate_task_cost
        for agent_name, model_name in self._active_models:
            cost = estimate_task_cost(model_name, 100_000, 1_000)
            self.assertIsNotNone(
                cost, f"{model_name} (agent {agent_name}) returned None cost",
            )
            self.assertIsInstance(
                cost, float, f"{model_name} returned non-float cost",
            )
            self.assertGreaterEqual(
                cost, 0, f"{model_name} returned negative cost",
            )

    def test_no_active_model_is_a_known_retired_provider(self):
        """Retired providers (qwen #102, gemini F12) must not appear in the active lineup.

        Negative-case fixture: ensures the lineup itself stays clean even if a
        retired provider gets re-added to agent_models.json by mistake.
        """
        from tasks.pricing import get_active_agents
        active = get_active_agents()
        retired = {"qwen", "gemini"}
        overlap = active & retired
        self.assertEqual(
            overlap, set(),
            f"Retired providers reappeared in active lineup: {sorted(overlap)}",
        )


class TestRetiredModelHistoricalCost(APITestCase):
    """A model retired from the active lineup must still price HISTORICAL tasks.

    Root cause: agent_models.json used absence-as-retirement, so once a model
    version rolled off the lineup, every already-completed task that used it
    got cost=None forever. The fix keeps retired entries (with last-known
    pricing) behind a `retired` flag instead of deleting them.
    """

    def setUp(self):
        super().setUp()
        _clear_pricing_cache()

    def test_retired_claude_model_version_prices_from_history(self):
        """claude-sonnet-4-5 (superseded by claude-sonnet-5) must still price."""
        from tasks.pricing import estimate_task_cost
        cost = estimate_task_cost("claude-sonnet-4-5", 100_000, 1_000)
        self.assertIsNotNone(
            cost, "Retired model 'claude-sonnet-4-5' must resolve a historical cost",
        )
        self.assertIsInstance(cost, float)
        self.assertGreater(cost, 0)

    def test_retired_provider_models_price_from_history(self):
        """Fully-retired providers (qwen, gemini) must still price their old models."""
        from tasks.pricing import estimate_task_cost
        for model_name in ("coder-model", "gemini-2.5-flash"):
            cost = estimate_task_cost(model_name, 100_000, 1_000)
            self.assertIsNotNone(
                cost, f"Retired model '{model_name}' must resolve a historical cost",
            )
            self.assertGreater(cost, 0)

    def test_retired_model_task_gets_computable_cost(self):
        """A completed task pinned to a retired model computes a real cost, not None."""
        board = self.make_board()
        task = self.make_task(board, model_name="claude-sonnet-4-5")
        _make_trace_comment(task, input_tokens=100_000, output_tokens=1_000, fmt="claude")

        from tasks.pricing import compute_task_estimated_cost
        cost = compute_task_estimated_cost(task)
        self.assertIsNotNone(
            cost, "Task using a retired model must still compute a cost from history",
        )

    def test_retired_models_excluded_from_active_lineup(self):
        """Retired models/agents keep their pricing but must not surface as active."""
        from tasks.pricing import get_active_agents, _agent_registry
        active = get_active_agents()
        self.assertNotIn("qwen", active)
        self.assertNotIn("gemini", active)

        claude_models = {
            m["name"]: m for m in _agent_registry()["claude"]["models"]
        }
        self.assertIn(
            "claude-sonnet-4-5", claude_models,
            "Retired model entry must remain in the registry for historical pricing",
        )
        self.assertTrue(
            claude_models["claude-sonnet-4-5"].get("retired"),
            "Retired model entry must be flagged retired",
        )


class TestExecutionResultUsageForwarding(APITestCase):
    """A completed task's diagnostics must show the real token usage the agent reported.

    Root cause: testing_tools/_utils.py::extract_token_parts() (used by
    task_inspect.py, board_overview.py, spec_trace.py) read the deprecated
    metadata.last_usage field directly. That field was intentionally retired
    in favor of on-the-fly computation from the trace comment
    (compute_usage_from_trace — see test_execution_results.py's
    test_usage_from_trace_not_from_metadata for that architectural
    decision), but the diagnostic tooling was never migrated — so
    task_inspect.py always showed "(not captured)" regardless of what the
    agent actually reported.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def test_task_inspect_shows_real_token_counts(self):
        task = self.make_task(self.board)
        raw_output = "\n".join([
            '{"type":"text","part":{"text":"done"}}',
            json.dumps({"modelUsage": {"claude-sonnet-5": {
                "inputTokens": 4200,
                "outputTokens": 850,
                "cacheReadInputTokens": 100,
                "cacheCreationInputTokens": 10,
            }}}),
        ])
        # Simulate the orchestrator posting the trace comment — the
        # authoritative source compute_usage_from_trace() reads from.
        TaskComment.objects.create(
            task=task,
            author_email="odin@system",
            content=raw_output,
            attachments=["trace:execution_jsonl"],
        )
        resp = self.client.post(
            f"/tasks/{task.id}/execution_result/",
            {
                "execution_result": {
                    "success": True,
                    "raw_output": raw_output,
                    "duration_ms": 1500.0,
                    "agent": "claude",
                    "metadata": {"selected_model": "claude-sonnet-5"},
                },
                "status": "REVIEW",
                "updated_by": "claude@odin.agent",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        task.refresh_from_db()

        import sys
        from pathlib import Path
        testing_tools_dir = str(Path(__file__).resolve().parent.parent / "testing_tools")
        if testing_tools_dir not in sys.path:
            sys.path.insert(0, testing_tools_dir)
        from _utils import extract_token_parts

        total, inp, out = extract_token_parts(task)
        self.assertEqual(
            total, 5050,
            "task_inspect.py must show the agent's real token counts, not zero",
        )
        self.assertEqual(inp, 4200)
        self.assertEqual(out, 850)

