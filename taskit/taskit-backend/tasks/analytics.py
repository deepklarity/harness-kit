"""Cost & Analytics endpoint — server-side aggregation of cost data.

Single endpoint that returns all analytics data needed for the /analytics
page. Uses the same cost computation chain as the serializers
(compute_usage_from_trace → estimate_task_cost) but aggregates server-side.

W3.17: also surfaces autonomy (operator touch rate, agent-authored merge
rate), failure-class breakdown (from metadata.failure_class), rework-
round distribution (from metadata.rework_count), and exec-duration
percentiles. Reuses ``autonomy_metrics.compute_board_metrics`` so the
autonomy numbers stay in lockstep with the diagnostic script.
"""

import asyncio
import logging
import sys
from collections import defaultdict
from pathlib import Path

from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .execution_processing import compute_usage_from_trace
from .models import Board, MergeAttempt, ReflectionReport, Spec, Task
from .pricing import estimate_task_cost
from .views import _apply_date_range, _parse_multi_values

logger = logging.getLogger(__name__)

# Lazy import: autonomy_metrics is a diagnostic script under testing_tools/
# that calls setup_django() at module import. The script is a sibling of
# the backend package, so we add its parent to sys.path once and import.
_AUTONOMY_METRICS = None


def _get_autonomy_metrics():
    """Lazy singleton import of testing_tools.autonomy_metrics.

    Avoids a hard import at module load (the script lives outside the
    tasks package, so it must be on sys.path before we can ``import`` it).
    """
    global _AUTONOMY_METRICS
    if _AUTONOMY_METRICS is not None:
        return _AUTONOMY_METRICS
    tools_dir = str(Path(__file__).resolve().parent.parent / "testing_tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import autonomy_metrics  # noqa: WPS433 — lazy import by design
    _AUTONOMY_METRICS = autonomy_metrics
    return _AUTONOMY_METRICS


def _extract_agent(email: str) -> str:
    """Return the short agent name extracted from an email like 'plan+opus@odin.agent'.

    Single source of truth for the bucket name — the cost_by_agent
    chart and the per-agent rollup must agree on the same names.
    Anything not matching the agent pattern becomes the literal
    ``"human"`` or ``"unknown"`` bucket.
    """
    if not email:
        return "unknown"
    if email.endswith("@odin.agent"):
        local = email.split("@")[0]
        plus_idx = local.find("+")
        return local[:plus_idx] if plus_idx != -1 else local
    if email == "odin@harness.kit":
        return "odin"
    return "human"


@api_view(["GET"])
def quota_status(request):
    """Return current usage/quota data for all configured AI providers.

    Uses harness_usage_status to fetch live quota info. Returns [] if
    the package is not installed or no providers are configured.
    """
    try:
        from harness_usage_status.config import load_config
        from harness_usage_status.providers.registry import get_all_providers
    except ImportError:
        return Response([])

    try:
        config = load_config()
        providers = get_all_providers(config.get_provider_configs())
    except Exception:
        logger.exception("Failed to load harness_usage_status config/providers")
        return Response([])

    now = timezone.now()
    now_iso = now.isoformat()

    async def _fetch_all():
        results = []
        for name, provider in providers.items():
            try:
                usage = await provider.get_usage()
                status = await provider.get_status()
                usage.compute_pct()
                raw = usage.raw or {}
                results.append({
                    "provider": usage.provider,
                    "plan": usage.plan,
                    "usage_pct": usage.usage_pct,
                    "used": usage.used,
                    "limit": usage.quota_limit,
                    "remaining": usage.remaining,
                    "unit": usage.unit,
                    "reset_date": usage.reset_date.isoformat() if usage.reset_date else None,
                    "state": status.state.value if status.state else None,
                    "error": raw.get("error"),
                    "last_fetched": now_iso,
                    "raw": usage.raw,
                })
            except Exception:
                logger.warning("Failed to fetch quota for provider %s", name, exc_info=True)
        return results

    try:
        data = asyncio.run(_fetch_all())
    except RuntimeError:
        # Already in an async event loop (e.g. ASGI) — use nest_asyncio or skip
        try:
            import nest_asyncio
            nest_asyncio.apply()
            data = asyncio.run(_fetch_all())
        except ImportError:
            loop = asyncio.get_event_loop()
            data = loop.run_until_complete(_fetch_all())

    return Response(data)


@api_view(["GET"])
def cost_summary(request):
    """Aggregate cost analytics across tasks.

    Query params:
        board_id / board — filter to specific board(s), comma-separated
        date_from — ISO date/datetime lower bound on created_at
        date_to — ISO date/datetime upper bound on created_at
        granularity — day | week | month (default: day)
    """
    qp = request.query_params

    board_ids = _parse_multi_values(qp, "board_id", aliases=("board",))
    granularity = qp.get("granularity", "day")
    if granularity not in ("day", "week", "month"):
        granularity = "day"

    qs = Task.objects.select_related("assignee", "board").prefetch_related("comments")
    if board_ids:
        qs = qs.filter(board_id__in=board_ids)
    qs = _apply_date_range(qs, qp, "created_at", "date_from", "date_to")
    tasks = list(qs)

    cost_data = _build_task_cost_data(tasks)
    task_ids = [t.id for t in tasks]
    reflection_data = _build_reflection_cost_data(task_ids)
    merge_cost = _build_merge_cost(task_ids)
    plan_cost = _build_plan_cost(tasks)

    board_ids_in_data = {d["board_id"] for d in cost_data if d.get("board_id")}
    if board_ids:
        board_ids_in_data.update(int(b) for b in board_ids)
    board_names = {}
    if board_ids_in_data:
        board_names = dict(
            Board.objects.filter(id__in=board_ids_in_data).values_list("id", "name")
        )

    # Autonomy + rework + failure-class + duration rollups — feed the
    # "wave health" cards on the analytics page. Uses the diagnostic
    # script's pure compute function so the on-page numbers and the
    # CLI script agree to the digit.
    autonomy = _compute_autonomy_rollup(tasks, board_ids)

    # W12.4 stats-rebuild sections: throughput funnel, league, per_spec,
    # scheduled_tasks. All four are mutually exclusive + exhaustive so
    # the bucket counts add to the denominator exactly (the bug the
    # user reported: 66% pass + 15% rework with 19% missing).
    throughput_funnel = compute_throughput_funnel(tasks)
    league = _build_league_section(board_ids)
    per_spec = _build_per_spec_rollup(board_ids)
    scheduled_tasks = _build_scheduled_tasks_rollup(board_ids)

    return Response({
        "summary_kpis": _compute_summary_kpis(cost_data, reflection_data, plan_cost, merge_cost),
        "time_series": _aggregate_time_series(cost_data, granularity),
        "cost_by_model": _aggregate_by_model(cost_data),
        "cost_by_board": _aggregate_by_board(cost_data, board_names),
        "cost_by_agent": _aggregate_by_agent(cost_data),
        "efficiency_metrics": _compute_efficiency_metrics(cost_data, reflection_data),
        "model_comparison": _build_model_comparison(cost_data),
        "top_expensive_tasks": _get_top_expensive_tasks(cost_data, limit=10),
        "autonomy": autonomy,
        "failure_class_breakdown": _failure_class_breakdown(tasks),
        "rework_breakdown": _rework_round_breakdown(tasks),
        "per_agent_rollup": _per_agent_rollup(cost_data, tasks),
        "merge_health": _merge_health_breakdown(task_ids),
        "review_health": _review_health_breakdown(task_ids),
        "throughput_funnel": throughput_funnel,
        "league": league,
        "per_spec": per_spec,
        "scheduled_tasks": scheduled_tasks,
        "meta": {
            "task_count": len(tasks),
            "granularity": granularity,
        },
    })


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_task_cost_data(tasks):
    """Iterate tasks and produce a materialized list of cost records."""
    records = []
    for task in tasks:
        usage = compute_usage_from_trace(task)
        md = task.metadata or {}
        model = task.model_name or md.get("selected_model") or md.get("model")
        input_tokens = usage.get("input_tokens") or 0
        output_tokens = usage.get("output_tokens") or 0
        total_tokens = usage.get("total_tokens") or 0
        cache_read = usage.get("cache_read_input_tokens") or 0
        cache_creation = usage.get("cache_creation_input_tokens") or 0
        cost = estimate_task_cost(model, input_tokens or None, output_tokens or None) if model else None
        duration_ms = md.get("last_duration_ms")

        assignee_email = ""
        if task.assignee:
            assignee_email = task.assignee.email or ""

        records.append({
            "task_id": task.id,
            "title": task.title,
            "status": task.status,
            "model": model or "",
            "cost": cost,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
            "duration_ms": duration_ms,
            "created_at": task.created_at,
            "last_updated_at": task.last_updated_at,
            "board_id": task.board_id,
            "assignee_email": assignee_email,
        })
    return records


def _build_reflection_cost_data(task_ids):
    """Compute reflection costs from ReflectionReport for the given tasks."""
    if not task_ids:
        return []
    reflections = ReflectionReport.objects.filter(
        task_id__in=task_ids,
        status="COMPLETED",
    ).values_list("reviewer_model", "token_usage", "duration_ms")
    records = []
    for reviewer_model, token_usage, duration_ms in reflections:
        usage = token_usage or {}
        r_input = usage.get("input_tokens") or 0
        r_output = usage.get("output_tokens") or 0
        cost = estimate_task_cost(reviewer_model, r_input or None, r_output or None) if reviewer_model else None
        records.append({
            "model": reviewer_model or "",
            "cost": cost,
            "input_tokens": r_input,
            "output_tokens": r_output,
            "duration_ms": duration_ms,
        })
    return records


def _build_merge_cost(task_ids):
    """Sum merge-agent costs from MergeAttempt rows for the given tasks."""
    if not task_ids:
        return 0.0
    total = 0.0
    for agent_model, token_usage in MergeAttempt.objects.filter(
        task_id__in=task_ids,
    ).values_list("agent_model", "token_usage"):
        usage = token_usage or {}
        cost = estimate_task_cost(
            agent_model,
            usage.get("input_tokens") or None,
            usage.get("output_tokens") or None,
        ) if agent_model else None
        if cost is not None:
            total += cost
    return total


def _build_plan_cost(tasks):
    """Sum plan costs from spec planning_trace metadata.

    Each task carries a ``spec_id``; we collect the distinct specs and
    read ``planning_trace.token_usage`` + ``planning_trace.model`` from
    each. Uses the same ``estimate_task_cost`` as every other category
    so /stats and the spec page agree to the digit.
    """
    spec_ids = {t.spec_id for t in tasks if t.spec_id}
    if not spec_ids:
        return 0.0
    total = 0.0
    for spec_meta in Spec.objects.filter(id__in=spec_ids).values_list("metadata", flat=True):
        trace = (spec_meta or {}).get("planning_trace") or {}
        usage = trace.get("token_usage") or {}
        model = trace.get("model")
        if model and usage:
            cost = estimate_task_cost(
                model,
                usage.get("input_tokens") or None,
                usage.get("output_tokens") or None,
            )
            if cost is not None:
                total += cost
    return total


def _compute_summary_kpis(cost_data, reflection_data, plan_cost=0.0, merge_cost=0.0):
    total_spend = sum(d["cost"] or 0 for d in cost_data)
    total_tokens = sum(d["total_tokens"] for d in cost_data)
    task_count = len(cost_data)
    tasks_with_cost = [d for d in cost_data if d["cost"] is not None and d["cost"] > 0]
    avg_cost = (total_spend / len(tasks_with_cost)) if tasks_with_cost else 0
    reflection_cost = sum(d["cost"] or 0 for d in reflection_data)
    # total_spend means ALL money: plan + build + review + merge. The
    # frontend and CSV read this one number — nobody recomputes it.
    build_spend = total_spend
    total_spend = build_spend + plan_cost + reflection_cost + merge_cost
    return {
        "total_spend": round(total_spend, 4),
        "build_spend": round(build_spend, 4),
        "total_tokens": total_tokens,
        "task_count": task_count,
        "avg_cost_per_task": round(avg_cost, 4),
        "reflection_cost": round(reflection_cost, 4),
        "plan_cost": round(plan_cost, 4),
        "merge_cost": round(merge_cost, 4),
    }


def _aggregate_time_series(cost_data, granularity):
    """Group cost data into time buckets with per-model breakdown.

    Also tallies a "landed" task_count per bucket — DONE tasks bucketed by
    their last_updated_at (falling back to created_at), which is the closest
    proxy we have to a completion date since Task has no dedicated done_at
    field. This is a separate bucketing dimension from the cost total (which
    stays keyed by created_at), so the two series are unioned by date key.
    """
    buckets = defaultdict(lambda: defaultdict(float))
    bucket_totals = defaultdict(float)
    landed_counts = defaultdict(int)

    for d in cost_data:
        if d["created_at"]:
            key = _time_bucket_key(d["created_at"], granularity)
            cost = d["cost"] or 0
            model = d["model"] or "unknown"
            buckets[key][model] += cost
            bucket_totals[key] += cost
        if d["status"] == "DONE":
            landed_at = d.get("last_updated_at") or d["created_at"]
            if landed_at:
                landed_key = _time_bucket_key(landed_at, granularity)
                landed_counts[landed_key] += 1

    all_keys = sorted(set(bucket_totals.keys()) | set(landed_counts.keys()))
    result = []
    for key in all_keys:
        result.append({
            "date": key,
            "total": round(bucket_totals.get(key, 0.0), 4),
            "by_model": {m: round(v, 4) for m, v in sorted(buckets.get(key, {}).items())},
            "task_count": landed_counts.get(key, 0),
        })
    return result


def _time_bucket_key(dt, granularity):
    """Return a string key for the time bucket."""
    if granularity == "week":
        # ISO week: Monday as start
        iso = dt.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    elif granularity == "month":
        return dt.strftime("%Y-%m")
    else:
        return dt.strftime("%Y-%m-%d")


def _aggregate_by_model(cost_data):
    """Group totals by model name."""
    models = defaultdict(lambda: {"cost": 0.0, "tokens": 0, "task_count": 0})
    for d in cost_data:
        model = d["model"] or "unknown"
        models[model]["cost"] += d["cost"] or 0
        models[model]["tokens"] += d["total_tokens"]
        models[model]["task_count"] += 1
    return [
        {
            "model": model,
            "cost": round(info["cost"], 4),
            "tokens": info["tokens"],
            "task_count": info["task_count"],
        }
        for model, info in sorted(models.items(), key=lambda x: -x[1]["cost"])
    ]


def _aggregate_by_board(cost_data, board_names):
    """Group totals by board."""
    boards = defaultdict(lambda: {"cost": 0.0, "task_count": 0})
    for d in cost_data:
        bid = d["board_id"]
        if bid is None:
            continue
        boards[bid]["cost"] += d["cost"] or 0
        boards[bid]["task_count"] += 1
    return [
        {
            "board_id": bid,
            "board_name": board_names.get(bid, f"Board {bid}"),
            "cost": round(info["cost"], 4),
            "task_count": info["task_count"],
        }
        for bid, info in sorted(boards.items(), key=lambda x: -x[1]["cost"])
    ]


def _aggregate_by_agent(cost_data):
    """Group by agent provider extracted from assignee email.

    Assignee emails follow the pattern: {agent}+{model}@odin.agent
    E.g., 'plan+opus@odin.agent' → agent='plan', provider='claude' (inferred from model)
    For simplicity, group by the agent name (before the +).
    """
    agents = defaultdict(lambda: {"cost": 0.0, "tokens": 0, "task_count": 0})
    for d in cost_data:
        agent = _extract_agent(d["assignee_email"])
        agents[agent]["cost"] += d["cost"] or 0
        agents[agent]["tokens"] += d["total_tokens"]
        agents[agent]["task_count"] += 1
    return [
        {
            "agent": agent,
            "cost": round(info["cost"], 4),
            "tokens": info["tokens"],
            "task_count": info["task_count"],
        }
        for agent, info in sorted(agents.items(), key=lambda x: -x[1]["cost"])
    ]


def _compute_efficiency_metrics(cost_data, reflection_data):
    """Compute efficiency-related metrics."""
    # `input_tokens` from the trace parser is fresh (non-cached) input only —
    # cache reads are reported separately and are additive to it, not a
    # subset. The denominator must be total context tokens served
    # (fresh + cached), otherwise the rate can exceed 100% (task 240 on
    # board 5 showed 1262%, a bogus reading on the retrospective page).
    total_cache_read = sum(d["cache_read_tokens"] for d in cost_data)
    total_input = sum(d["input_tokens"] for d in cost_data)
    total_context = total_cache_read + total_input
    cache_hit_rate = (total_cache_read / total_context * 100) if total_context > 0 else 0

    failed = [d for d in cost_data if d["status"] == "FAILED"]
    failure_cost = sum(d["cost"] or 0 for d in failed)

    tasks_with_cost = [d for d in cost_data if d["cost"] is not None and d["cost"] > 0]
    avg_cost = (sum(d["cost"] for d in tasks_with_cost) / len(tasks_with_cost)) if tasks_with_cost else 0

    reflection_cost = sum(d["cost"] or 0 for d in reflection_data)

    tasks_with_tokens = [d for d in cost_data if d["total_tokens"] > 0]
    avg_tokens = (sum(d["total_tokens"] for d in tasks_with_tokens) / len(tasks_with_tokens)) if tasks_with_tokens else 0

    tasks_with_duration = [d for d in cost_data if d["duration_ms"] and d["duration_ms"] > 0]
    avg_duration = (sum(d["duration_ms"] for d in tasks_with_duration) / len(tasks_with_duration)) if tasks_with_duration else 0

    return {
        "cache_hit_rate": round(cache_hit_rate, 1),
        "failure_cost": round(failure_cost, 4),
        "avg_cost_per_task": round(avg_cost, 4),
        "reflection_cost": round(reflection_cost, 4),
        "avg_tokens_per_task": round(avg_tokens),
        "avg_duration_ms": round(avg_duration),
        "failed_task_count": len(failed),
        "total_task_count": len(cost_data),
    }


def _build_model_comparison(cost_data):
    """Per-model comparison table: avg cost, duration, success rate, tokens."""
    models = defaultdict(lambda: {
        "costs": [],
        "durations": [],
        "tokens": [],
        "success_count": 0,
        "total_count": 0,
        "reflection_pass": 0,
        "reflection_total": 0,
    })
    for d in cost_data:
        model = d["model"] or "unknown"
        info = models[model]
        info["total_count"] += 1
        if d["cost"] is not None:
            info["costs"].append(d["cost"])
        if d["duration_ms"] and d["duration_ms"] > 0:
            info["durations"].append(d["duration_ms"])
        if d["total_tokens"] > 0:
            info["tokens"].append(d["total_tokens"])
        if d["status"] in ("DONE", "TESTING", "REVIEW"):
            info["success_count"] += 1

    task_model_map = {d["task_id"]: (d["model"] or "unknown") for d in cost_data}
    if task_model_map:
        for r in ReflectionReport.objects.filter(
            task_id__in=task_model_map.keys(),
            status="COMPLETED",
        ).values("task_id", "verdict"):
            model = task_model_map.get(r["task_id"], "unknown")
            if model in models:
                models[model]["reflection_total"] += 1
                if r["verdict"] == "PASS":
                    models[model]["reflection_pass"] += 1

    result = []
    for model, info in sorted(models.items(), key=lambda x: -sum(x[1]["costs"]) if x[1]["costs"] else 0):
        avg_cost = (sum(info["costs"]) / len(info["costs"])) if info["costs"] else 0
        avg_duration = (sum(info["durations"]) / len(info["durations"])) if info["durations"] else 0
        avg_tokens = (sum(info["tokens"]) / len(info["tokens"])) if info["tokens"] else 0
        success_rate = (info["success_count"] / info["total_count"] * 100) if info["total_count"] > 0 else 0
        reflection_pass_rate = (
            round(info["reflection_pass"] / info["reflection_total"] * 100, 1)
            if info["reflection_total"] > 0 else None
        )
        result.append({
            "model": model,
            "avg_cost": round(avg_cost, 4),
            "total_cost": round(sum(info["costs"]), 4) if info["costs"] else 0,
            "avg_duration_ms": round(avg_duration),
            "avg_tokens": round(avg_tokens),
            "success_rate": round(success_rate, 1),
            "task_count": info["total_count"],
            "reflection_pass_rate": reflection_pass_rate,
        })
    return result


def _get_top_expensive_tasks(cost_data, limit=10):
    """Return top N tasks sorted by cost descending."""
    with_cost = [d for d in cost_data if d["cost"] is not None and d["cost"] > 0]
    with_cost.sort(key=lambda d: d["cost"], reverse=True)
    return [
        {
            "task_id": d["task_id"],
            "title": d["title"],
            "model": d["model"],
            "status": d["status"],
            "cost": round(d["cost"], 4),
            "total_tokens": d["total_tokens"],
        }
        for d in with_cost[:limit]
    ]


# ── W3.17 rollups ─────────────────────────────────────────────────


def _compute_autonomy_rollup(tasks, board_ids):
    """Return the autonomy scorecard for the filtered task set.

    Reuses ``autonomy_metrics.compute_board_metrics`` so the numbers on
    the analytics page and the CLI diagnostic agree to the digit. The
    function expects either a Board or a Spec; we pass ``None`` for both
    when board_ids is empty (whole-DB scope) and the first matching
    Board otherwise. For multi-board queries the function falls back to
    a direct rollup (the script's per-board assumption is documented in
    its docstring — multi-board aggregation has to be a sum, not a
    reuse of the script).
    """
    if not tasks:
        return {
            "total_done": 0,
            "agent_authored": 0,
            "autonomy_rate": 0.0,
            "operator_touches_total": 0,
            "tasks_with_capture_gaps": 0,
            "exec_duration_seconds": {"min": 0, "max": 0, "p50": 0, "p90": 0},
            "dispatch_to_done_seconds": {"min": 0, "max": 0, "p50": 0, "p90": 0},
        }

    if len(board_ids or []) != 1:
        # Multi-board (or no board filter): the diagnostic script scopes
        # to one board at a time, so reuse the helpers and aggregate by
        # hand. Each Board gets its own compute_board_metrics call.
        return _aggregate_autonomy_across_boards(board_ids)

    # Single board — pass the Board to the script so the script's
    # per-board branch runs unchanged.
    try:
        board = Board.objects.get(pk=board_ids[0])
    except Board.DoesNotExist:
        return _aggregate_autonomy_across_boards(None)
    autonomy = _get_autonomy_metrics()
    return autonomy.compute_board_metrics(board=board)


def _aggregate_autonomy_across_boards(board_ids):
    """Sum autonomy metrics across all matching boards (or whole DB).

    Used when the analytics query spans multiple boards (or none). The
    diagnostic script's ``compute_board_metrics`` is per-board, so we
    loop and sum the relevant fields directly.
    """
    autonomy = _get_autonomy_metrics()
    boards = list(Board.objects.all()) if not board_ids else list(
        Board.objects.filter(pk__in=board_ids)
    )
    if not boards:
        return {
            "total_done": 0,
            "agent_authored": 0,
            "autonomy_rate": 0.0,
            "operator_touches_total": 0,
            "tasks_with_capture_gaps": 0,
            "exec_duration_seconds": {"min": 0, "max": 0, "p50": 0, "p90": 0},
            "dispatch_to_done_seconds": {"min": 0, "max": 0, "p50": 0, "p90": 0},
        }

    total_done = 0
    agent_authored = 0
    operator_touches = 0
    capture_gaps = 0
    exec_samples = []
    d2d_samples = []

    for board in boards:
        m = autonomy.compute_board_metrics(board=board)
        total_done += m["total_done"]
        agent_authored += m["agent_authored"]
        operator_touches += m["operator_touches_total"]
        capture_gaps += m["cost"]["tasks_with_capture_gaps"]
        for k in ("min", "max", "p50", "p90"):
            v = m["exec_duration_seconds"][k]
            if v:
                exec_samples.append(v)
            v2 = m["dispatch_to_done_seconds"][k]
            if v2:
                d2d_samples.append(v2)

    return {
        "total_done": total_done,
        "agent_authored": agent_authored,
        "autonomy_rate": (agent_authored / total_done) if total_done else 0.0,
        "operator_touches_total": operator_touches,
        "tasks_with_capture_gaps": capture_gaps,
        "exec_duration_seconds": _percentile_dict(exec_samples),
        "dispatch_to_done_seconds": _percentile_dict(d2d_samples),
    }


def _percentile_dict(samples):
    """Min/max/p50/p90 from a list of samples (0-filled when empty)."""
    if not samples:
        return {"min": 0, "max": 0, "p50": 0, "p90": 0}
    sorted_s = sorted(samples)
    p50_idx = int((len(sorted_s) - 1) * 0.5)
    p90_idx = int((len(sorted_s) - 1) * 0.9)
    return {
        "min": sorted_s[0],
        "max": sorted_s[-1],
        "p50": sorted_s[p50_idx],
        "p90": sorted_s[p90_idx],
    }


def _failure_class_breakdown(tasks):
    """Count FAILED tasks by metadata.failure_class.

    Source: ``tag_failure_class`` (failure_tagger.py) writes the label
    on every FAILED transition. Only tasks still in FAILED status are
    counted — tasks that recovered (FAILED → IN_PROGRESS → DONE) carry
    the cost in ``rework_breakdown`` instead, so the operator can see
    the waste without confusing it for a still-failing cohort. Returns
    a zeroed structure when nothing failed.
    """
    buckets: dict[str, int] = defaultdict(int)
    for t in tasks:
        if t.status != "FAILED":
            continue
        meta = t.metadata or {}
        cls = meta.get("failure_class")
        if cls:
            buckets[cls] += 1
    if not buckets:
        return {"buckets": [], "total_failed": 0}
    return {
        "buckets": sorted(
            (
                {"class": k, "count": v}
                for k, v in buckets.items()
            ),
            key=lambda x: -x["count"],
        ),
        "total_failed": sum(buckets.values()),
    }


def _rework_round_breakdown(tasks):
    """Distribution of rework_count across the task set.

    Source: ``task.metadata["rework_count"]`` bumped by
    ``_record_rework_continuity`` (F45 continuity + infra auto-redispatch)
    and ``_maybe_reassign_on_quota_failure`` (F159 quota reassign).
    Buckets: 0 / 1 / 2 / 3+ — past 3 the long tail flattens on the page.
    """
    buckets = {"0": 0, "1": 0, "2": 0, "3+": 0}
    for t in tasks:
        meta = t.metadata or {}
        n = int(meta.get("rework_count", 0) or 0)
        if n == 0:
            buckets["0"] += 1
        elif n == 1:
            buckets["1"] += 1
        elif n == 2:
            buckets["2"] += 1
        else:
            buckets["3+"] += 1
    return [
        {"rounds": k, "tasks": v} for k, v in buckets.items()
    ]


def _merge_health_breakdown(task_ids):
    """Merge ladder health for the "is the merge ladder working" section.

    Source: ``MergeAttempt`` (task #209) — one row per rung of the merge
    ladder (static git merge -> merge agent -> human resume). Groups by
    mode and outcome and reports dispatch-to-finish lag percentiles.
    Returns a zeroed structure when the board has no merge attempts yet
    (never omit the section or crash).
    """
    attempts = list(MergeAttempt.objects.filter(task_id__in=task_ids))
    if not attempts:
        return {"total_attempts": 0, "by_mode": [], "by_outcome": [], "lag_seconds": _percentile_dict([])}

    by_mode: dict[str, int] = defaultdict(int)
    by_outcome: dict[str, int] = defaultdict(int)
    lag_samples = []
    for a in attempts:
        by_mode[a.mode] += 1
        by_outcome[a.outcome] += 1
        if a.started_at and a.finished_at:
            lag_samples.append((a.finished_at - a.started_at).total_seconds())

    return {
        "total_attempts": len(attempts),
        "by_mode": sorted(
            ({"mode": k, "count": v} for k, v in by_mode.items()),
            key=lambda x: -x["count"],
        ),
        "by_outcome": sorted(
            ({"outcome": k, "count": v} for k, v in by_outcome.items()),
            key=lambda x: -x["count"],
        ),
        "lag_seconds": _percentile_dict(lag_samples),
    }


def _review_health_breakdown(task_ids):
    """Reflection verdict distribution for the "is review catching things"
    section. Source: ``ReflectionReport.verdict``. Returns a zeroed
    structure when the board has no reflection reports yet.
    """
    verdicts = list(
        ReflectionReport.objects.filter(task_id__in=task_ids)
        .exclude(verdict="")
        .values_list("verdict", flat=True)
    )
    if not verdicts:
        return {"total_reviews": 0, "by_verdict": []}

    counts: dict[str, int] = defaultdict(int)
    for v in verdicts:
        counts[v] += 1
    return {
        "total_reviews": len(verdicts),
        "by_verdict": sorted(
            ({"verdict": k, "count": v} for k, v in counts.items()),
            key=lambda x: -x["count"],
        ),
    }


def _per_agent_rollup(cost_data, tasks):
    """Per-agent rollup: cost, tokens, tasks, rework, agent-authored.

    A single source for the "per-wave/per-agent" table on the page. The
    cost side reuses the same task-cost records the cost_by_agent chart
    already uses; rework and autonomy come from metadata + autonomy
    classification so the operator can spot a high-cost agent that also
    burns cycles on retries.
    """
    by_agent: dict[str, dict] = defaultdict(lambda: {
        "cost": 0.0, "tokens": 0, "tasks": 0,
        "rework_rounds": 0, "rework_tasks": 0, "agent_authored": 0,
    })
    # Index tasks by id for fast metadata + autonomy lookup.
    task_by_id = {t.id: t for t in tasks}
    autonomy = _get_autonomy_metrics()

    for d in cost_data:
        agent = _extract_agent(d["assignee_email"])
        by_agent[agent]["cost"] += d["cost"] or 0
        by_agent[agent]["tokens"] += d["total_tokens"]
        by_agent[agent]["tasks"] += 1
        task = task_by_id.get(d["task_id"])
        if task is not None:
            meta = task.metadata or {}
            n = int(meta.get("rework_count", 0) or 0)
            by_agent[agent]["rework_rounds"] += n
            if n > 0:
                by_agent[agent]["rework_tasks"] += 1

    # Autonomy classification per task — only for DONE tasks (matches
    # the script's contract). Classify is cheap enough at analytics time.
    for task in tasks:
        if task.status != "DONE":
            continue
        try:
            cls = autonomy.classify_task(task)
        except Exception:
            logger.warning("autonomy classify failed for task %s", task.id, exc_info=True)
            continue
        agent = _extract_agent(getattr(task.assignee, "email", "") or "")
        if cls["agent_authored"]:
            by_agent[agent]["agent_authored"] += 1

    return [
        {
            "agent": agent,
            "cost": round(info["cost"], 4),
            "tokens": info["tokens"],
            "tasks": info["tasks"],
            "rework_rounds": info["rework_rounds"],
            "rework_tasks": info["rework_tasks"],
            "agent_authored": info["agent_authored"],
        }
        for agent, info in sorted(by_agent.items(), key=lambda x: -x[1]["cost"])
    ]


# Module-level `_extract_agent` is defined near the top of this file —
# single source of truth shared by the cost_by_agent chart and the
# per-agent rollup.


# ---------------------------------------------------------------------------
# W12.4 stats-rebuild helpers
# ---------------------------------------------------------------------------


# Throughput funnel bucket order. The order is the visual order on the
# stats page and the canonical denominator ordering — sum to 100% top
# to bottom.
FUNNEL_BUCKETS = ("pass", "rework", "fail", "in_flight")


def compute_throughput_funnel(tasks):
    """Partition a task list into 4 mutually exclusive, exhaustive buckets.

    The funnel replaces the prior review_pass + rework_rate pair, which
    didn't share a denominator and could show 66% pass + 15% rework
    with 19% silently missing. After this helper every view sums to
    100% on a visible denominator.

    Buckets:
      pass       — DONE tasks with metadata.rework_count == 0 (clean
                   landing, no rework)
      rework     — DONE tasks with metadata.rework_count > 0 (landed
                   but had to be redone at least once)
      fail       — FAILED tasks (terminal failure; not counted as rework
                   even if they were retried, because rework is
                   specifically about "landed after redos")
      in_flight  — every other status (BACKLOG, TODO, IN_PROGRESS,
                   EXECUTING, REVIEW, TESTING, CANCELED, plus DONE
                   tasks with no rework metadata and FAILED without
                   any capture — defensive)

    The function takes a list of Task-like objects (id, status,
    metadata) so callers can pass ORM querysets, plain lists, or test
    fixtures without changing the math.
    """
    counts = {b: 0 for b in FUNNEL_BUCKETS}
    for t in tasks:
        status = (getattr(t, "status", "") or "").upper()
        meta = getattr(t, "metadata", None) or {}
        rework = int(meta.get("rework_count", 0) or 0)

        if status == "DONE":
            if rework > 0:
                counts["rework"] += 1
            else:
                counts["pass"] += 1
        elif status == "FAILED":
            counts["fail"] += 1
        else:
            counts["in_flight"] += 1

    total = sum(counts.values())
    if total == 0:
        return {
            "total": 0,
            "buckets": [
                {"bucket": b, "count": 0, "pct": 0.0} for b in FUNNEL_BUCKETS
            ],
        }

    # Round each pct to whole numbers and patch the largest bucket to
    # absorb the rounding residual so the visible percentages sum to
    # exactly 100. The bucket counts always sum to total exactly — the
    # rounding is purely a display concern.
    raw = [(b, counts[b], (counts[b] / total) * 100.0) for b in FUNNEL_BUCKETS]
    rounded = [(b, c, round(p)) for (b, c, p) in raw]
    residual = 100 - sum(p for (_, _, p) in rounded)
    if residual != 0 and rounded:
        # Pick the bucket with the largest fractional remainder whose
        # rounding error is in the right direction.
        idx = max(
            range(len(rounded)),
            key=lambda i: (
                abs(raw[i][2] - rounded[i][2]),
                rounded[i][1],
            ),
        )
        rounded[idx] = (rounded[idx][0], rounded[idx][1], rounded[idx][2] + residual)

    return {
        "total": total,
        "buckets": [
            {"bucket": b, "count": c, "pct": float(p)}
            for (b, c, p) in rounded
        ],
    }


def _build_league_section(board_ids):
    """Per-(agent, model) league rollup, scoped to one board or all boards.

    For the single-board view this matches the data shape of the
    existing /api/boards/<id>/league/ endpoint. For the All-Boards
    view (board_ids empty or contains many ids) it aggregates across
    every board so the page can render the same league table the
    operator sees on a single board.

    The per-board path delegates to ``tasks.league.compute_league_for_board``
    so the response shape, sorting, and operator-takeover rules stay
    identical — the only difference is whether the scope is one board
    or all of them.
    """
    from .league import compute_league_for_board

    if board_ids and len(board_ids) == 1:
        try:
            board = Board.objects.get(pk=board_ids[0])
        except Board.DoesNotExist:
            board = None
        rows = compute_league_for_board(board=board)
        return {
            "rows": [r.to_dict() for r in rows],
            "meta": {
                "task_count": sum(r.tasks_landed for r in rows),
                "board_id": board_ids[0] if board else None,
                "since_spec": None,
                "aggregate": False,
            },
        }

    # All-boards (or no single-board scope) — aggregate.
    rows = compute_league_for_board(board=None)
    return {
        "rows": [r.to_dict() for r in rows],
        "meta": {
            "task_count": sum(r.tasks_landed for r in rows),
            "board_id": None,
            "since_spec": None,
            "aggregate": True,
        },
    }


def _build_per_spec_rollup(board_ids):
    """One row per spec, sorted newest-first, with cost + outcome counts.

    Reuses ``compute_spec_cost_summary`` so the per-spec cost number
    matches the spec-detail endpoint (single source of truth). The
    outcome counts (done_count, failed_count, in_flight_count) are
    added so the operator can see at a glance which specs are landing
    cleanly vs. spinning their wheels, without drilling into each
    spec's story.
    """
    from .models import Spec
    from .pricing import compute_spec_cost_summary

    specs_qs = Spec.objects.select_related("board").order_by("-created_at")
    if board_ids:
        specs_qs = specs_qs.filter(board_id__in=board_ids)

    rows = []
    for spec in specs_qs:
        tasks = list(spec.tasks.all())
        if not tasks:
            continue
        cost = compute_spec_cost_summary(tasks)
        done_count = sum(1 for t in tasks if (t.status or "").upper() == "DONE")
        failed_count = sum(1 for t in tasks if (t.status or "").upper() == "FAILED")
        in_flight_count = len(tasks) - done_count - failed_count
        rows.append({
            "odin_id": spec.odin_id,
            "title": spec.title,
            "board_id": spec.board_id,
            "board_name": spec.board.name if spec.board_id else None,
            "task_count": len(tasks),
            "done_count": done_count,
            "failed_count": failed_count,
            "in_flight_count": in_flight_count,
            "total_cost_usd": cost.get("total_cost_usd", 0.0),
            "total_tokens": cost.get("total_tokens", 0),
            "created_at": spec.created_at.isoformat() if spec.created_at else None,
        })
    return rows


def _build_scheduled_tasks_rollup(board_ids):
    """Per-schedule rollup with run history success/failure counts.

    Surfaces the same data the operator gets on the schedules page
    but compacted: one row per schedule with run_count, success_count,
    and failure_count so a portfolio owner can scan the All-Boards
    view and see which cron-style tasks are healthy.
    """
    from .models import ScheduleRunStatus, TaskSchedule

    qs = TaskSchedule.objects.select_related("board").order_by(
        "-created_at",
    )
    if board_ids:
        qs = qs.filter(board_id__in=board_ids)

    rows = []
    for sched in qs:
        runs = list(sched.runs.all())
        if not runs:
            rows.append({
                "id": sched.id,
                "template_title": sched.template_title,
                "template_kind": sched.kind,
                "status": sched.status,
                "board_id": sched.board_id,
                "board_name": sched.board.name if sched.board_id else None,
                "run_count": 0,
                "success_count": 0,
                "failure_count": 0,
                "next_run_at_utc": (
                    sched.next_run_at_utc.isoformat()
                    if sched.next_run_at_utc else None
                ),
                "created_at": sched.created_at.isoformat() if sched.created_at else None,
            })
            continue
        success_count = sum(
            1 for r in runs
            if r.status == ScheduleRunStatus.COMPLETED_SUCCESS
        )
        failure_count = sum(
            1 for r in runs
            if r.status == ScheduleRunStatus.COMPLETED_FAILED
        )
        rows.append({
            "id": sched.id,
            "template_title": sched.template_title,
            "template_kind": sched.kind,
            "status": sched.status,
            "board_id": sched.board_id,
            "board_name": sched.board.name if sched.board_id else None,
            "run_count": len(runs),
            "success_count": success_count,
            "failure_count": failure_count,
            "next_run_at_utc": (
                sched.next_run_at_utc.isoformat()
                if sched.next_run_at_utc else None
            ),
            "created_at": sched.created_at.isoformat() if sched.created_at else None,
        })
    return rows
