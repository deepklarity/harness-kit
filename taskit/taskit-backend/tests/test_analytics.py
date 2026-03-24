"""Tests for the analytics cost-summary endpoint."""

import json

from .base import APITestCase
from tasks.models import ReflectionReport, TaskComment


def _clear_pricing_cache():
    from tasks.pricing import get_pricing_table
    get_pricing_table.cache_clear()


def _make_trace_comment(task, input_tokens, output_tokens):
    """Create a trace comment simulating orchestrator output (Claude format)."""
    content = "\n".join([
        '{"type":"text","part":{"text":"done"}}',
        json.dumps({"modelUsage": {"model": {
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
        }}}),
    ])
    return TaskComment.objects.create(
        task=task,
        author_email="odin@harness.kit",
        content=content[:50000],
        attachments=["trace:execution_jsonl"],
    )


class TestAnalyticsCostSummary(APITestCase):

    def setUp(self):
        super().setUp()
        _clear_pricing_cache()
        self.board = self.make_board(name="Analytics Board")
        self.user = self.make_user(name="Claude", email="plan+opus@odin.agent")

    def test_empty_returns_zeroed_structure(self):
        """Endpoint returns all sections even with no data."""
        resp = self.client.get("/api/analytics/cost-summary/")
        self.assertEqual(resp.status_code, 200)
        data = resp.data
        self.assertIn("summary_kpis", data)
        self.assertIn("time_series", data)
        self.assertIn("cost_by_model", data)
        self.assertIn("cost_by_board", data)
        self.assertIn("cost_by_agent", data)
        self.assertIn("efficiency_metrics", data)
        self.assertIn("model_comparison", data)
        self.assertIn("top_expensive_tasks", data)
        self.assertIn("meta", data)
        self.assertEqual(data["summary_kpis"]["task_count"], 0)
        self.assertEqual(data["summary_kpis"]["total_spend"], 0)

    def test_single_task_with_cost(self):
        """A task with trace tokens appears in all relevant sections."""
        task = self.make_task(
            self.board,
            title="Expensive task",
            model_name="claude-sonnet-4-5-20250929",
            assignee=self.user,
        )
        _make_trace_comment(task, input_tokens=100_000, output_tokens=50_000)

        resp = self.client.get("/api/analytics/cost-summary/")
        self.assertEqual(resp.status_code, 200)
        data = resp.data

        # Summary KPIs
        kpis = data["summary_kpis"]
        self.assertEqual(kpis["task_count"], 1)
        self.assertGreater(kpis["total_spend"], 0)
        self.assertEqual(kpis["total_tokens"], 150_000)

        # Cost by model
        self.assertTrue(len(data["cost_by_model"]) > 0)
        model_entry = data["cost_by_model"][0]
        self.assertEqual(model_entry["task_count"], 1)
        self.assertGreater(model_entry["cost"], 0)

        # Cost by board
        self.assertTrue(len(data["cost_by_board"]) > 0)
        board_entry = data["cost_by_board"][0]
        self.assertEqual(board_entry["board_name"], "Analytics Board")

        # Cost by agent (plan+opus@odin.agent → agent "plan")
        agent_entries = data["cost_by_agent"]
        agent_names = [a["agent"] for a in agent_entries]
        self.assertIn("plan", agent_names)

        # Top expensive tasks
        self.assertTrue(len(data["top_expensive_tasks"]) > 0)
        top = data["top_expensive_tasks"][0]
        self.assertEqual(top["title"], "Expensive task")
        self.assertGreater(top["cost"], 0)

        # Model comparison
        self.assertTrue(len(data["model_comparison"]) > 0)

        # Time series
        self.assertTrue(len(data["time_series"]) > 0)

    def test_board_filter(self):
        """Only tasks from the filtered board appear."""
        board2 = self.make_board(name="Other Board")
        self.make_task(self.board, title="Task A", model_name="claude-sonnet-4-5-20250929")
        self.make_task(board2, title="Task B", model_name="claude-sonnet-4-5-20250929")

        resp = self.client.get(f"/api/analytics/cost-summary/?board={self.board.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["summary_kpis"]["task_count"], 1)

    def test_date_range_filter(self):
        """Date range filters restrict tasks by created_at."""
        self.make_task(self.board, title="Recent task")

        # Very old date range — should return 0
        resp = self.client.get("/api/analytics/cost-summary/?date_from=2020-01-01&date_to=2020-12-31")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["summary_kpis"]["task_count"], 0)

        # Very wide date range — should return 1
        resp = self.client.get("/api/analytics/cost-summary/?date_from=2020-01-01&date_to=2030-12-31")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["summary_kpis"]["task_count"], 1)

    def test_granularity_param(self):
        """Granularity is reflected in meta and affects time series keys."""
        self.make_task(self.board, title="Task", model_name="claude-sonnet-4-5-20250929")

        for gran in ("day", "week", "month"):
            resp = self.client.get(f"/api/analytics/cost-summary/?granularity={gran}")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.data["meta"]["granularity"], gran)

    def test_invalid_granularity_defaults_to_day(self):
        resp = self.client.get("/api/analytics/cost-summary/?granularity=invalid")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["meta"]["granularity"], "day")

    def test_reflection_cost_included(self):
        """Reflection costs appear in summary KPIs and efficiency metrics."""
        task = self.make_task(
            self.board,
            title="Reflected task",
            model_name="claude-sonnet-4-5-20250929",
        )
        ReflectionReport.objects.create(
            task=task,
            reviewer_agent="claude",
            reviewer_model="claude-sonnet-4-5-20250929",
            status="COMPLETED",
            requested_by="test@test.com",
            token_usage={"input_tokens": 10_000, "output_tokens": 5_000},
        )

        resp = self.client.get("/api/analytics/cost-summary/")
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(resp.data["summary_kpis"]["reflection_cost"], 0)
        self.assertGreater(resp.data["efficiency_metrics"]["reflection_cost"], 0)

    def test_efficiency_metrics_cache_hit_rate(self):
        """Cache hit rate is computed from cache_read vs input tokens."""
        task = self.make_task(
            self.board,
            title="Cached task",
            model_name="claude-sonnet-4-5-20250929",
        )
        # Create a trace with cache read tokens
        content = "\n".join([
            '{"type":"text","part":{"text":"done"}}',
            json.dumps({"modelUsage": {"model": {
                "inputTokens": 100_000,
                "outputTokens": 50_000,
                "cacheReadInputTokens": 80_000,
            }}}),
        ])
        TaskComment.objects.create(
            task=task,
            author_email="odin@harness.kit",
            content=content[:50000],
            attachments=["trace:execution_jsonl"],
        )

        resp = self.client.get("/api/analytics/cost-summary/")
        self.assertEqual(resp.status_code, 200)
        # cache_hit_rate = 80k / 100k * 100 = 80%
        self.assertAlmostEqual(resp.data["efficiency_metrics"]["cache_hit_rate"], 80.0, places=0)

    def test_failed_tasks_in_efficiency(self):
        """Failed tasks contribute to failure cost."""
        task = self.make_task(
            self.board,
            title="Failed task",
            model_name="claude-sonnet-4-5-20250929",
            status="FAILED",
        )
        _make_trace_comment(task, input_tokens=50_000, output_tokens=20_000)

        resp = self.client.get("/api/analytics/cost-summary/")
        self.assertEqual(resp.status_code, 200)
        eff = resp.data["efficiency_metrics"]
        self.assertEqual(eff["failed_task_count"], 1)
        self.assertGreater(eff["failure_cost"], 0)
