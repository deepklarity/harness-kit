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
    model_usage_line = json.dumps({"modelUsage": {"claude-sonnet-4-5": {
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
        self.assertIn("claude-sonnet-4-5", table)

    def test_pricing_table_known_model(self):
        from tasks.pricing import get_pricing_table
        table = get_pricing_table()
        entry = table["claude-sonnet-4-5"]
        self.assertEqual(entry["input_price_per_1m_tokens"], 3.00)
        self.assertEqual(entry["output_price_per_1m_tokens"], 15.00)

    def test_pricing_table_qwen_has_pricing(self):
        from tasks.pricing import get_pricing_table
        table = get_pricing_table()
        entry = table["qwen3-coder"]
        self.assertEqual(entry["input_price_per_1m_tokens"], 1.00)
        self.assertEqual(entry["output_price_per_1m_tokens"], 5.00)

    def test_pricing_table_nonexistent_model(self):
        from tasks.pricing import get_pricing_table
        table = get_pricing_table()
        self.assertNotIn("totally-fake-model-xyz", table)

    def test_estimate_task_cost_known(self):
        from tasks.pricing import estimate_task_cost
        cost = estimate_task_cost("claude-sonnet-4-5", 1000, 500)
        # (1000/1M)*3.00 + (500/1M)*15.00 = 0.003 + 0.0075 = 0.0105
        self.assertAlmostEqual(cost, 0.0105, places=6)

    def test_estimate_task_cost_qwen(self):
        from tasks.pricing import estimate_task_cost
        cost = estimate_task_cost("qwen3-coder", 1000, 500)
        # (1000/1M)*1.00 + (500/1M)*5.00 = 0.001 + 0.0025 = 0.0035
        self.assertAlmostEqual(cost, 0.0035, places=6)

    def test_estimate_task_cost_unknown_model(self):
        from tasks.pricing import estimate_task_cost
        cost = estimate_task_cost("totally-fake-model-xyz", 1000, 500)
        self.assertIsNone(cost)

    def test_estimate_task_cost_null_tokens(self):
        from tasks.pricing import estimate_task_cost
        cost = estimate_task_cost("claude-sonnet-4-5", None, None)
        self.assertIsNone(cost)

    def test_estimate_task_cost_nonexistent_model(self):
        from tasks.pricing import estimate_task_cost
        cost = estimate_task_cost("nonexistent-model", 1000, 500)
        self.assertIsNone(cost)


class TestTaskDetailCostEstimation(APITestCase):
    """Test that task detail endpoint includes estimated cost (from trace comments)."""

    def test_task_detail_includes_estimated_cost(self):
        board = self.make_board()
        task = self.make_task(board, model_name="claude-sonnet-4-5")
        _make_trace_comment(task, input_tokens=10000, output_tokens=5000)
        resp = self.client.get(f"/tasks/{task.id}/detail/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("estimated_cost_usd", resp.data)
        # (10000/1M)*3.00 + (5000/1M)*15.00 = 0.03 + 0.075 = 0.105
        self.assertAlmostEqual(resp.data["estimated_cost_usd"], 0.105, places=4)

    def test_task_detail_null_usage_returns_null_cost(self):
        board = self.make_board()
        task = self.make_task(board, model_name="claude-sonnet-4-5")
        # No trace comment → no usage → null cost
        resp = self.client.get(f"/tasks/{task.id}/detail/")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.data["estimated_cost_usd"])

    def test_task_detail_qwen_returns_cost(self):
        board = self.make_board()
        task = self.make_task(board, model_name="qwen3-coder")
        _make_trace_comment(task, input_tokens=10000, output_tokens=5000, fmt="gemini")
        resp = self.client.get(f"/tasks/{task.id}/detail/")
        self.assertEqual(resp.status_code, 200)
        # (10000/1M)*1.00 + (5000/1M)*5.00 = 0.01 + 0.025 = 0.035
        self.assertAlmostEqual(resp.data["estimated_cost_usd"], 0.035, places=4)

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
        task = self.make_task(board, model_name="claude-sonnet-4-5")
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
        task = self.make_task(board, model_name="claude-sonnet-4-5")
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
        task = self.make_task(board, model_name="claude-sonnet-4-5")
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
        task = self.make_task(board, spec=spec, model_name="qwen3-coder")
        _make_trace_comment(task, input_tokens=5000, output_tokens=2000, fmt="gemini")
        resp = self.client.get(f"/specs/{spec.id}/")
        self.assertEqual(resp.status_code, 200)
        tasks = resp.data["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertAlmostEqual(tasks[0]["estimated_cost_usd"], 0.015, places=4)

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
            board, title="Task 1", spec=spec, model_name="claude-sonnet-4-5",
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
        t1 = self.make_task(board, title="T1", spec=spec, model_name="claude-sonnet-4-5")
        _make_trace_comment(t1, input_tokens=8000, output_tokens=2000, total_tokens=10000)
        t2 = self.make_task(board, title="T2", spec=spec, model_name="qwen3-coder")
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
        t1 = self.make_task(board, title="Task 1", spec=spec, model_name="claude-sonnet-4-5")
        _make_trace_comment(t1, input_tokens=10000, output_tokens=5000, total_tokens=15000)
        # Task with known pricing and usage (qwen)
        t2 = self.make_task(board, title="Task 2", spec=spec, model_name="qwen3-coder")
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
        # claude: (10000/1M)*3.00 + (5000/1M)*15.00 = 0.105
        # qwen:   (5000/1M)*1.00  + (2000/1M)*5.00  = 0.015
        # total = 0.12
        self.assertAlmostEqual(summary["total_cost_usd"], 0.12, places=4)
        self.assertEqual(summary["total_tokens"], 23500)  # 15000 + 7000 + 1500
        self.assertEqual(summary["tasks_with_unknown_cost"], 1)  # fake model; no-usage task not counted
        self.assertIn("claude-sonnet-4-5", summary["cost_by_model"])
        self.assertIn("qwen3-coder", summary["cost_by_model"])

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
    """Verify EVERY model across all 6 harnesses has valid pricing.

    This is the canary test: if any model in agent_models.json has null
    pricing, the UI shows "unknown" cost which is exactly the bug we're
    preventing. Tests cover: claude, codex, gemini, qwen, minimax, glm.
    """

    # Every model from agent_models.json with expected pricing
    EXPECTED_PRICING = {
        # Claude harness
        "claude-sonnet-4-5":   (3.00, 15.00),
        "claude-opus-4":       (15.00, 75.00),
        "claude-opus-4-6":     (15.00, 75.00),
        "claude-haiku-4-5":    (0.80, 4.00),
        # Codex harness
        "gpt-5.3-codex":      (2.00, 8.00),
        "o3":                  (10.00, 40.00),
        "o4-mini":             (1.10, 4.40),
        # Gemini harness
        "gemini-2.5-pro":           (1.25, 10.00),
        "gemini-2.5-flash":         (0.15, 0.60),
        "gemini-2.0-flash":         (0.10, 0.40),
        "gemini-3-pro-preview":     (1.25, 10.00),
        "gemini-3-flash-preview":   (0.15, 0.60),
        # Qwen harness
        "qwen3-coder":        (1.00, 5.00),
        # MiniMax harness
        "minimax-coding-plan/MiniMax-M2":   (0.255, 1.00),
        "minimax-coding-plan/MiniMax-M2.1": (0.27, 0.95),
        "minimax-coding-plan/MiniMax-M2.5": (0.30, 1.10),
        # GLM harness
        "zai-coding-plan/glm-5":           (1.00, 3.20),
        "zai-coding-plan/glm-4.7":         (0.60, 2.20),
        "zai-coding-plan/glm-4.7-flash":   (0.00, 0.00),
        "zai-coding-plan/glm-4.6":         (0.60, 2.20),
        "zai-coding-plan/glm-4.6v":        (0.30, 0.90),
        "zai-coding-plan/glm-4.5-air":     (0.20, 1.10),
        "zai-coding-plan/glm-4.5-flash":   (0.00, 0.00),
        "zai-coding-plan/glm-4.5v":        (0.60, 1.80),
    }

    def setUp(self):
        super().setUp()
        _clear_pricing_cache()

    def test_all_models_present_in_pricing_table(self):
        """Every expected model must appear in the pricing table."""
        from tasks.pricing import get_pricing_table
        table = get_pricing_table()
        for model in self.EXPECTED_PRICING:
            self.assertIn(model, table, f"Model {model} missing from pricing table")

    def test_no_model_has_null_pricing(self):
        """No model should have null input or output pricing."""
        from tasks.pricing import get_pricing_table
        table = get_pricing_table()
        for model in self.EXPECTED_PRICING:
            entry = table[model]
            self.assertIsNotNone(
                entry["input_price_per_1m_tokens"],
                f"{model} has null input pricing"
            )
            self.assertIsNotNone(
                entry["output_price_per_1m_tokens"],
                f"{model} has null output pricing"
            )

    def test_all_models_match_expected_pricing(self):
        """Pricing values must match what we researched."""
        from tasks.pricing import get_pricing_table
        table = get_pricing_table()
        for model, (exp_input, exp_output) in self.EXPECTED_PRICING.items():
            entry = table[model]
            self.assertAlmostEqual(
                entry["input_price_per_1m_tokens"], exp_input, places=3,
                msg=f"{model} input price mismatch"
            )
            self.assertAlmostEqual(
                entry["output_price_per_1m_tokens"], exp_output, places=3,
                msg=f"{model} output price mismatch"
            )

    def test_all_models_produce_numeric_cost(self):
        """estimate_task_cost should return a float (not None) for every model."""
        from tasks.pricing import estimate_task_cost
        for model in self.EXPECTED_PRICING:
            cost = estimate_task_cost(model, 100_000, 1_000)
            self.assertIsNotNone(cost, f"{model} returned None cost")
            self.assertIsInstance(cost, float, f"{model} returned non-float cost")
            self.assertGreaterEqual(cost, 0, f"{model} returned negative cost")

