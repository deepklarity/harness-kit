"""Tests for the analytics cost-summary endpoint."""

import json

from datetime import timedelta

from django.utils import timezone

from .base import APITestCase
from tasks.models import MergeAttempt, MergeMode, MergeOutcome, MergeTrigger, ReflectionReport, TaskComment


def _clear_pricing_cache():
    from tasks.pricing import get_pricing_table
    get_pricing_table.cache_clear()


def _priced_model():
    """Return a model name currently present in agent_models.json pricing.

    The analytics tests need a model the pricing table recognizes so cost > 0.
    Resolve from agent_models.json (single source) instead of pinning a literal
    — the active lineup changes over time and tests must survive that.
    """
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


class TestAnalyticsCostSummary(APITestCase):

    def setUp(self):
        super().setUp()
        _clear_pricing_cache()
        self.board = self.make_board(name="Analytics Board")
        self.user = self.make_user(name="Claude", email="plan+opus@odin.agent")
        self.priced_model = _priced_model()

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
        # W3.17 — autonomy + failure + rework + per-agent rollups
        self.assertIn("autonomy", data)
        self.assertIn("failure_class_breakdown", data)
        self.assertIn("rework_breakdown", data)
        self.assertIn("per_agent_rollup", data)
        self.assertEqual(data["summary_kpis"]["task_count"], 0)
        self.assertEqual(data["summary_kpis"]["total_spend"], 0)
        # Empty state — every section is present and zeroed, never missing.
        self.assertEqual(data["autonomy"]["total_done"], 0)
        self.assertEqual(data["autonomy"]["autonomy_rate"], 0.0)
        self.assertEqual(data["failure_class_breakdown"]["total_failed"], 0)
        self.assertEqual(data["failure_class_breakdown"]["buckets"], [])
        # rework_breakdown always has the four canonical buckets
        # (0/1/2/3+) so the page can render placeholders consistently.
        bucket_keys = {b["rounds"] for b in data["rework_breakdown"]}
        self.assertEqual(bucket_keys, {"0", "1", "2", "3+"})

    def test_single_task_with_cost(self):
        """A task with trace tokens appears in all relevant sections."""
        task = self.make_task(
            self.board,
            title="Expensive task",
            model_name=self.priced_model,
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
        self.make_task(self.board, title="Task A", model_name=self.priced_model)
        self.make_task(board2, title="Task B", model_name=self.priced_model)

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
        self.make_task(self.board, title="Task", model_name=self.priced_model)

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
            model_name=self.priced_model,
        )
        ReflectionReport.objects.create(
            task=task,
            reviewer_agent="claude",
            reviewer_model=self.priced_model,
            status="COMPLETED",
            requested_by="test@test.com",
            token_usage={"input_tokens": 10_000, "output_tokens": 5_000},
        )

        resp = self.client.get("/api/analytics/cost-summary/")
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(resp.data["summary_kpis"]["reflection_cost"], 0)
        self.assertGreater(resp.data["efficiency_metrics"]["reflection_cost"], 0)

    def test_efficiency_metrics_cache_hit_rate(self):
        """Cache hit rate = cache_read / (cache_read + fresh input) tokens.

        `inputTokens` from Claude Code's modelUsage is fresh (non-cached)
        input only — cache reads are reported separately in
        `cacheReadInputTokens`, additive to `inputTokens`, not a subset of
        it. The denominator must be total context tokens (fresh + cached),
        not fresh alone, or the rate can exceed 100%.
        """
        task = self.make_task(
            self.board,
            title="Cached task",
            model_name=self.priced_model,
        )
        # Create a trace with cache read tokens
        content = "\n".join([
            '{"type":"text","part":{"text":"done"}}',
            json.dumps({"modelUsage": {"model": {
                "inputTokens": 20_000,
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
        # cache_hit_rate = 80k / (80k + 20k) * 100 = 80%
        self.assertAlmostEqual(resp.data["efficiency_metrics"]["cache_hit_rate"], 80.0, places=0)

    def test_efficiency_metrics_cache_hit_rate_never_exceeds_100(self):
        """Real long-running-agent traces have cache_read >> fresh input
        (a big system prompt re-read cached across many turns while fresh
        input per turn stays small). The rate must stay a valid percentage.
        """
        task = self.make_task(
            self.board,
            title="Long cached task",
            model_name=self.priced_model,
        )
        content = "\n".join([
            '{"type":"text","part":{"text":"done"}}',
            json.dumps({"modelUsage": {"model": {
                "inputTokens": 10_000,
                "outputTokens": 5_000,
                "cacheReadInputTokens": 4_000_000,
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
        rate = resp.data["efficiency_metrics"]["cache_hit_rate"]
        self.assertLessEqual(rate, 100.0)
        self.assertGreater(rate, 99.0)

    def test_failed_tasks_in_efficiency(self):
        """Failed tasks contribute to failure cost."""
        task = self.make_task(
            self.board,
            title="Failed task",
            model_name=self.priced_model,
            status="FAILED",
        )
        _make_trace_comment(task, input_tokens=50_000, output_tokens=20_000)

        resp = self.client.get("/api/analytics/cost-summary/")
        self.assertEqual(resp.status_code, 200)
        eff = resp.data["efficiency_metrics"]
        self.assertEqual(eff["failed_task_count"], 1)
        self.assertGreater(eff["failure_cost"], 0)

    # ── W3.17 — autonomy / failure / rework / per-agent rollups ─────

    def test_autonomy_section_appears_with_done_tasks(self):
        """Autonomy scorecard is present and reflects DONE-task count.

        A task with no DONE status contributes nothing to autonomy. We
        only assert the shape, not the rate (rate depends on operator
        touches which require a more elaborate fixture; the dedicated
        autonomy_metrics tests cover that math).
        """
        self.make_task(
            self.board, title="TODO task", model_name=self.priced_model,
        )
        resp = self.client.get("/api/analytics/cost-summary/")
        self.assertEqual(resp.status_code, 200)
        a = resp.data["autonomy"]
        self.assertIn("total_done", a)
        self.assertIn("agent_authored", a)
        self.assertIn("autonomy_rate", a)
        self.assertIn("operator_touches_total", a)
        self.assertIn("exec_duration_seconds", a)
        self.assertIn("dispatch_to_done_seconds", a)
        # No DONE tasks → zeroed rollup.
        self.assertEqual(a["total_done"], 0)

    def test_failure_class_breakdown_counts_metadata_tag(self):
        """Tasks with metadata.failure_class appear in the breakdown.

        We bypass the executor and stamp the tag directly (the
        failure_tagger unit tests cover the classification; this test
        pins the analytics integration).
        """
        t1 = self.make_task(
            self.board, title="Quota fail", model_name=self.priced_model, status="FAILED",
        )
        t1.metadata = {"failure_class": "quota_exhaustion"}
        t1.save(update_fields=["metadata"])
        t2 = self.make_task(
            self.board, title="Timeout fail", model_name=self.priced_model, status="FAILED",
        )
        t2.metadata = {"failure_class": "timeout"}
        t2.save(update_fields=["metadata"])
        # Third task with the same class — verify the count adds up.
        t3 = self.make_task(
            self.board, title="Quota fail 2", model_name=self.priced_model, status="FAILED",
        )
        t3.metadata = {"failure_class": "quota_exhaustion"}
        t3.save(update_fields=["metadata"])

        resp = self.client.get("/api/analytics/cost-summary/")
        self.assertEqual(resp.status_code, 200)
        breakdown = resp.data["failure_class_breakdown"]
        self.assertEqual(breakdown["total_failed"], 3)
        buckets = {b["class"]: b["count"] for b in breakdown["buckets"]}
        self.assertEqual(buckets.get("quota_exhaustion"), 2)
        self.assertEqual(buckets.get("timeout"), 1)
        # Sorted by count desc — quota_exhaustion must be first.
        self.assertEqual(breakdown["buckets"][0]["class"], "quota_exhaustion")

    def test_failure_class_breakdown_ignores_unfailed_tasks(self):
        """DONE tasks with stray failure_class in metadata are NOT counted.

        A task that succeeded shouldn't pollute the failure breakdown.
        The ``failure_class`` tag is a record of a failed attempt; tasks
        that ultimately succeeded carry the cost in ``rework_breakdown``
        instead, where the operator can see the waste without confusing
        it for a still-failing cohort.
        """
        t = self.make_task(
            self.board, title="Done task", model_name=self.priced_model, status="DONE",
        )
        t.metadata = {"failure_class": "quota_exhaustion"}  # stale tag
        t.save(update_fields=["metadata"])

        resp = self.client.get("/api/analytics/cost-summary/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.data["failure_class_breakdown"]["total_failed"], 0,
            "DONE tasks with stray failure_class must not appear in the "
            "failure breakdown — only the FAILED cohort is counted.",
        )

    def test_rework_breakdown_buckets_by_metadata(self):
        """Rework distribution buckets the tasks by metadata.rework_count."""
        for n, title in enumerate(["clean", "once", "twice", "thrice", "lots"]):
            t = self.make_task(
                self.board, title=title, model_name=self.priced_model,
            )
            t.metadata = {"rework_count": n}
            t.save(update_fields=["metadata"])

        resp = self.client.get("/api/analytics/cost-summary/")
        self.assertEqual(resp.status_code, 200)
        buckets = {b["rounds"]: b["tasks"] for b in resp.data["rework_breakdown"]}
        self.assertEqual(buckets["0"], 1)
        self.assertEqual(buckets["1"], 1)
        self.assertEqual(buckets["2"], 1)
        # 3 and 4 both go into the 3+ long-tail bucket.
        self.assertEqual(buckets["3+"], 2)

    def test_rework_breakdown_handles_missing_metadata(self):
        """Tasks without rework_count default to the 0 bucket."""
        self.make_task(
            self.board, title="No metadata", model_name=self.priced_model,
        )
        resp = self.client.get("/api/analytics/cost-summary/")
        self.assertEqual(resp.status_code, 200)
        buckets = {b["rounds"]: b["tasks"] for b in resp.data["rework_breakdown"]}
        self.assertEqual(buckets["0"], 1)

    def test_per_agent_rollup_groups_by_email_local_part(self):
        """The rollup groups by the agent short name extracted from email.

        `plan+opus@odin.agent` → "plan"; the test asserts both buckets
        and that the per-agent section sums match cost_by_agent for
        cost+task_count.
        """
        # setUp already created a user with email ``plan+opus@odin.agent``
        # — reuse it for the "plan" bucket instead of recreating.
        u_plan = self.user
        u_codex = self.make_user(name="codex", email="codex+gpt-5@odin.agent")
        t1 = self.make_task(
            self.board, title="Plan work", model_name=self.priced_model, assignee=u_plan,
        )
        _make_trace_comment(t1, input_tokens=10_000, output_tokens=5_000)
        t2 = self.make_task(
            self.board, title="Codex work", model_name=self.priced_model, assignee=u_codex,
        )
        _make_trace_comment(t2, input_tokens=20_000, output_tokens=10_000)
        # And one task with rework_count on the plan agent.
        t1.metadata = {"rework_count": 2}
        t1.save(update_fields=["metadata"])

        resp = self.client.get("/api/analytics/cost-summary/")
        self.assertEqual(resp.status_code, 200)
        rollup = {r["agent"]: r for r in resp.data["per_agent_rollup"]}
        self.assertIn("plan", rollup)
        self.assertIn("codex", rollup)
        # Rework counters propagate from metadata.
        self.assertEqual(rollup["plan"]["rework_rounds"], 2)
        self.assertEqual(rollup["plan"]["rework_tasks"], 1)
        self.assertEqual(rollup["codex"]["rework_rounds"], 0)
        self.assertEqual(rollup["codex"]["rework_tasks"], 0)
        # Per-agent task_count matches cost_by_agent for the same agent.
        by_agent_chart = {
            a["agent"]: a["task_count"] for a in resp.data["cost_by_agent"]
        }
        self.assertEqual(rollup["plan"]["tasks"], by_agent_chart.get("plan"))
        self.assertEqual(rollup["codex"]["tasks"], by_agent_chart.get("codex"))

    def test_merge_health_breakdown_by_mode_and_outcome(self):
        """Merge health rolls up MergeAttempt rows by mode + outcome +
        dispatch-to-finish lag, scoped to the current board filter — the
        retrospective page's "is the merge ladder working" section.
        """
        t1 = self.make_task(self.board, title="Static merge task")
        t2 = self.make_task(self.board, title="Agent merge task")
        t3 = self.make_task(self.board, title="Human merge task")
        started = timezone.now()
        MergeAttempt.objects.create(
            task=t1, trigger=MergeTrigger.REFLECTION_PASS, mode=MergeMode.STATIC,
            outcome=MergeOutcome.MERGED, started_at=started,
            finished_at=started + timedelta(seconds=5),
        )
        MergeAttempt.objects.create(
            task=t2, trigger=MergeTrigger.REFLECTION_PASS, mode=MergeMode.AGENT,
            outcome=MergeOutcome.MERGED, started_at=started,
            finished_at=started + timedelta(seconds=30),
        )
        MergeAttempt.objects.create(
            task=t3, trigger=MergeTrigger.HUMAN_RESUME, mode=MergeMode.HUMAN_ASSISTED,
            outcome=MergeOutcome.CONFLICT, started_at=started,
            finished_at=started + timedelta(seconds=120),
        )

        resp = self.client.get(f"/api/analytics/cost-summary/?board={self.board.id}")
        self.assertEqual(resp.status_code, 200)
        health = resp.data["merge_health"]
        self.assertEqual(health["total_attempts"], 3)
        by_mode = {b["mode"]: b["count"] for b in health["by_mode"]}
        self.assertEqual(by_mode, {"static": 1, "agent": 1, "human_assisted": 1})
        by_outcome = {b["outcome"]: b["count"] for b in health["by_outcome"]}
        self.assertEqual(by_outcome, {"merged": 2, "conflict": 1})
        self.assertAlmostEqual(health["lag_seconds"]["p50"], 30.0, places=0)

    def test_merge_health_empty_when_no_attempts(self):
        """No MergeAttempt rows -> zeroed structure, never a crash/None."""
        resp = self.client.get(f"/api/analytics/cost-summary/?board={self.board.id}")
        self.assertEqual(resp.status_code, 200)
        health = resp.data["merge_health"]
        self.assertEqual(health["total_attempts"], 0)
        self.assertEqual(health["by_mode"], [])
        self.assertEqual(health["by_outcome"], [])

    def test_review_health_verdict_distribution(self):
        """Review health rolls up ReflectionReport verdicts for the board."""
        t1 = self.make_task(self.board, title="Reviewed task 1")
        t2 = self.make_task(self.board, title="Reviewed task 2")
        t3 = self.make_task(self.board, title="Reviewed task 3")
        ReflectionReport.objects.create(
            task=t1, verdict="PASS", requested_by="test@test.com",
            reviewer_agent="claude", reviewer_model="claude-sonnet-5",
        )
        ReflectionReport.objects.create(
            task=t2, verdict="PASS", requested_by="test@test.com",
            reviewer_agent="claude", reviewer_model="claude-sonnet-5",
        )
        ReflectionReport.objects.create(
            task=t3, verdict="NEEDS_WORK", requested_by="test@test.com",
            reviewer_agent="codex", reviewer_model="gpt-5.5",
        )

        resp = self.client.get(f"/api/analytics/cost-summary/?board={self.board.id}")
        self.assertEqual(resp.status_code, 200)
        review = resp.data["review_health"]
        self.assertEqual(review["total_reviews"], 3)
        by_verdict = {b["verdict"]: b["count"] for b in review["by_verdict"]}
        self.assertEqual(by_verdict, {"PASS": 2, "NEEDS_WORK": 1})

    def test_review_health_empty_when_no_reports(self):
        """No ReflectionReport rows -> zeroed structure, never a crash/None."""
        resp = self.client.get(f"/api/analytics/cost-summary/?board={self.board.id}")
        self.assertEqual(resp.status_code, 200)
        review = resp.data["review_health"]
        self.assertEqual(review["total_reviews"], 0)
        self.assertEqual(review["by_verdict"], [])
