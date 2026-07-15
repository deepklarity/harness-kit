"""Tests for Plan/Build/Review/Merge cost attribution (task #343).

The spec cost rollup must sum all four categories — Plan, Build, Review,
Merge — from a single source of truth.  Plan cost comes from the spec's
``planning_trace`` metadata (model + token_usage recorded by the
planning-result endpoint).  Build cost is the existing task execution
rollup.  Review cost is the existing reflection rollup.  Merge cost is
the existing MergeAttempt rollup, now folded into the same dict.

The /stats (analytics cost-summary) endpoint must include the same
plan_cost and merge_cost numbers so the two surfaces agree.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

import json

from django.utils import timezone

from odin.worktree import MergeResult

from tasks.merge_recording import record_merge_attempt
from tasks.models import (
    MergeAttempt,
    MergeTrigger,
    ReflectionReport,
    TaskComment,
)
from tasks.pricing import compute_spec_cost_summary
from tests.base import APITestCase


def _clear_pricing_cache():
    from tasks.pricing import get_pricing_table
    get_pricing_table.cache_clear()


def _priced_model():
    """Return a model name present in agent_models.json pricing."""
    from tasks.pricing import get_agent_default_model
    return get_agent_default_model("claude")


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


class ComputeSpecCostSummaryTests(APITestCase):
    """compute_spec_cost_summary returns all four cost categories."""

    def setUp(self):
        super().setUp()
        _clear_pricing_cache()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board, odin_id="sp_343")
        self.priced_model = _priced_model()

    def test_returns_plan_and_merge_keys(self):
        """The summary dict includes plan_cost_usd and merge_cost_usd."""
        summary = compute_spec_cost_summary([], spec=self.spec)
        self.assertIn("plan_cost_usd", summary)
        self.assertIn("merge_cost_usd", summary)
        self.assertIn("total_cost_usd", summary)
        self.assertIn("reflection_cost_usd", summary)

    def test_plan_cost_from_spec_metadata(self):
        """Plan cost is derived from spec.metadata['planning_trace']['token_usage']."""
        self.spec.metadata = {
            "planning_trace": {
                "agent": "claude",
                "model": self.priced_model,
                "duration_ms": 30000,
                "success": True,
                "token_usage": {"input_tokens": 50_000, "output_tokens": 10_000},
            }
        }
        self.spec.save()

        summary = compute_spec_cost_summary([], spec=self.spec)
        self.assertGreater(summary["plan_cost_usd"], 0)

    def test_plan_cost_zero_when_no_planning_trace(self):
        """Plan cost is 0 when spec has no planning_trace."""
        summary = compute_spec_cost_summary([], spec=self.spec)
        self.assertEqual(summary["plan_cost_usd"], 0)

    def test_plan_cost_zero_when_no_token_usage(self):
        """Plan cost is 0 when planning_trace has no token_usage (old specs)."""
        self.spec.metadata = {
            "planning_trace": {
                "agent": "claude",
                "model": self.priced_model,
                "duration_ms": 30000,
                "success": True,
            }
        }
        self.spec.save()

        summary = compute_spec_cost_summary([], spec=self.spec)
        self.assertEqual(summary["plan_cost_usd"], 0)

    def test_merge_cost_included(self):
        """Merge cost from MergeAttempt rows is included in the summary."""
        task = self.make_task(self.board, spec=self.spec, title="T1")
        record_merge_attempt(
            task,
            MergeTrigger.REFLECTION_PASS,
            MergeResult(
                success=True,
                resolved_files=["a.py"],
                agent_model=self.priced_model,
                token_usage={"input_tokens": 1_000, "output_tokens": 500},
            ),
            timezone.now(),
            timezone.now(),
        )

        summary = compute_spec_cost_summary([task], spec=self.spec)
        self.assertGreater(summary["merge_cost_usd"], 0)

    def test_all_four_categories_summed(self):
        """Build + Review + Merge + Plan all contribute to the summary."""
        task = self.make_task(
            self.board, spec=self.spec, title="T1",
            model_name=self.priced_model,
        )
        _make_trace_comment(task, 20_000, 5_000)

        ReflectionReport.objects.create(
            task=task,
            reviewer_agent="claude",
            reviewer_model=self.priced_model,
            status="COMPLETED",
            requested_by="test@test.com",
            token_usage={"input_tokens": 10_000, "output_tokens": 2_000},
        )

        record_merge_attempt(
            task,
            MergeTrigger.REFLECTION_PASS,
            MergeResult(
                success=True,
                resolved_files=["a.py"],
                agent_model=self.priced_model,
                token_usage={"input_tokens": 1_000, "output_tokens": 500},
            ),
            timezone.now(),
            timezone.now(),
        )

        self.spec.metadata = {
            "planning_trace": {
                "agent": "claude",
                "model": self.priced_model,
                "duration_ms": 30000,
                "success": True,
                "token_usage": {"input_tokens": 50_000, "output_tokens": 10_000},
            }
        }
        self.spec.save()

        summary = compute_spec_cost_summary([task], spec=self.spec)
        self.assertGreater(summary["total_cost_usd"], 0)      # Build
        self.assertGreater(summary["reflection_cost_usd"], 0)  # Review
        self.assertGreater(summary["merge_cost_usd"], 0)       # Merge
        self.assertGreater(summary["plan_cost_usd"], 0)        # Plan

    def test_spec_none_omits_plan_cost(self):
        """When spec is None, plan_cost_usd is 0 (no source to read from)."""
        task = self.make_task(self.board, spec=self.spec, title="T1")
        summary = compute_spec_cost_summary([task], spec=None)
        self.assertEqual(summary["plan_cost_usd"], 0)


class PlanningResultTokenUsageTests(APITestCase):
    """POST /specs/:id/planning_result/ stores token_usage in metadata."""

    def setUp(self):
        super().setUp()
        _clear_pricing_cache()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board)
        self.priced_model = _priced_model()

    def test_planning_result_stores_token_usage(self):
        """token_usage is persisted in spec.metadata['planning_trace']."""
        resp = self.client.post(
            f"/specs/{self.spec.id}/planning_result/",
            {
                "raw_output": "Planning trace...",
                "duration_ms": 45000,
                "agent": "claude",
                "model": self.priced_model,
                "effective_input": "Plan prompt...",
                "success": True,
                "token_usage": {"input_tokens": 50_000, "output_tokens": 10_000},
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        self.spec.refresh_from_db()
        trace = self.spec.metadata["planning_trace"]
        self.assertEqual(trace["token_usage"]["input_tokens"], 50_000)
        self.assertEqual(trace["token_usage"]["output_tokens"], 10_000)

    def test_planning_result_without_token_usage(self):
        """Omitting token_usage still works (backward compatible)."""
        resp = self.client.post(
            f"/specs/{self.spec.id}/planning_result/",
            {
                "raw_output": "trace...",
                "duration_ms": 1000,
                "agent": "claude",
                "model": self.priced_model,
                "effective_input": "prompt...",
                "success": True,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

    def test_spec_detail_cost_summary_includes_plan_cost(self):
        """Spec detail API returns plan_cost_usd in cost_summary."""
        self.spec.metadata = {
            "planning_trace": {
                "agent": "claude",
                "model": self.priced_model,
                "duration_ms": 30000,
                "success": True,
                "token_usage": {"input_tokens": 50_000, "output_tokens": 10_000},
            }
        }
        self.spec.save()

        resp = self.client.get(f"/specs/{self.spec.id}/")
        self.assertEqual(resp.status_code, 200)
        cost_summary = resp.data["cost_summary"]
        self.assertIn("plan_cost_usd", cost_summary)
        self.assertIn("merge_cost_usd", cost_summary)
        self.assertGreater(cost_summary["plan_cost_usd"], 0)


class AnalyticsCostSummaryPlanMergeTests(APITestCase):
    """/stats (analytics cost-summary) includes plan_cost and merge_cost."""

    def setUp(self):
        super().setUp()
        _clear_pricing_cache()
        self.board = self.make_board(name="Analytics Board")
        self.priced_model = _priced_model()

    def test_summary_kpis_include_plan_cost(self):
        """plan_cost appears in summary_kpis when spec has planning_trace."""
        spec = self.make_spec(self.board, odin_id="sp_plan")
        spec.metadata = {
            "planning_trace": {
                "agent": "claude",
                "model": self.priced_model,
                "duration_ms": 30000,
                "success": True,
                "token_usage": {"input_tokens": 50_000, "output_tokens": 10_000},
            }
        }
        spec.save()
        self.make_task(self.board, spec=spec, title="T1")

        resp = self.client.get("/api/analytics/cost-summary/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("plan_cost", resp.data["summary_kpis"])
        self.assertGreater(resp.data["summary_kpis"]["plan_cost"], 0)

    def test_summary_kpis_include_merge_cost(self):
        """merge_cost appears in summary_kpis when MergeAttempt rows exist."""
        spec = self.make_spec(self.board, odin_id="sp_merge")
        task = self.make_task(self.board, spec=spec, title="T1")
        record_merge_attempt(
            task,
            MergeTrigger.REFLECTION_PASS,
            MergeResult(
                success=True,
                resolved_files=["a.py"],
                agent_model=self.priced_model,
                token_usage={"input_tokens": 1_000, "output_tokens": 500},
            ),
            timezone.now(),
            timezone.now(),
        )

        resp = self.client.get("/api/analytics/cost-summary/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("merge_cost", resp.data["summary_kpis"])
        self.assertGreater(resp.data["summary_kpis"]["merge_cost"], 0)

    def test_summary_kpis_zero_when_no_plan_or_merge(self):
        """plan_cost and merge_cost are 0 when no data exists."""
        self.make_task(self.board, title="Empty task")

        resp = self.client.get("/api/analytics/cost-summary/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["summary_kpis"]["plan_cost"], 0)
        self.assertEqual(resp.data["summary_kpis"]["merge_cost"], 0)
