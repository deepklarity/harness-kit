"""Cost & Analytics endpoint — server-side aggregation of cost data.

Single endpoint that returns all analytics data needed for the /analytics
page. Uses the same cost computation chain as the serializers
(compute_usage_from_trace → estimate_task_cost) but aggregates server-side.
"""

from collections import defaultdict

from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .execution_processing import compute_usage_from_trace
from .models import Board, ReflectionReport, Task
from .pricing import estimate_task_cost
from .views import _apply_date_range, _parse_multi_values


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

    board_ids_in_data = {d["board_id"] for d in cost_data if d.get("board_id")}
    if board_ids:
        board_ids_in_data.update(int(b) for b in board_ids)
    board_names = {}
    if board_ids_in_data:
        board_names = dict(
            Board.objects.filter(id__in=board_ids_in_data).values_list("id", "name")
        )

    return Response({
        "summary_kpis": _compute_summary_kpis(cost_data, reflection_data),
        "time_series": _aggregate_time_series(cost_data, granularity),
        "cost_by_model": _aggregate_by_model(cost_data),
        "cost_by_board": _aggregate_by_board(cost_data, board_names),
        "cost_by_agent": _aggregate_by_agent(cost_data),
        "efficiency_metrics": _compute_efficiency_metrics(cost_data, reflection_data),
        "model_comparison": _build_model_comparison(cost_data),
        "top_expensive_tasks": _get_top_expensive_tasks(cost_data, limit=10),
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


def _compute_summary_kpis(cost_data, reflection_data):
    """Top-level KPI numbers."""
    total_spend = sum(d["cost"] or 0 for d in cost_data)
    total_tokens = sum(d["total_tokens"] for d in cost_data)
    task_count = len(cost_data)
    tasks_with_cost = [d for d in cost_data if d["cost"] is not None and d["cost"] > 0]
    avg_cost = (total_spend / len(tasks_with_cost)) if tasks_with_cost else 0
    reflection_cost = sum(d["cost"] or 0 for d in reflection_data)
    return {
        "total_spend": round(total_spend, 4),
        "total_tokens": total_tokens,
        "task_count": task_count,
        "avg_cost_per_task": round(avg_cost, 4),
        "reflection_cost": round(reflection_cost, 4),
    }


def _aggregate_time_series(cost_data, granularity):
    """Group cost data into time buckets with per-model breakdown."""
    buckets = defaultdict(lambda: defaultdict(float))
    bucket_totals = defaultdict(float)

    for d in cost_data:
        if not d["created_at"]:
            continue
        key = _time_bucket_key(d["created_at"], granularity)
        cost = d["cost"] or 0
        model = d["model"] or "unknown"
        buckets[key][model] += cost
        bucket_totals[key] += cost

    result = []
    for key in sorted(buckets.keys()):
        result.append({
            "date": key,
            "total": round(bucket_totals[key], 4),
            "by_model": {m: round(v, 4) for m, v in sorted(buckets[key].items())},
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


def _extract_agent(email):
    """Extract agent name from email like 'plan+opus@odin.agent'."""
    if not email:
        return "unknown"
    if email.endswith("@odin.agent"):
        local = email.split("@")[0]
        plus_idx = local.find("+")
        return local[:plus_idx] if plus_idx != -1 else local
    if email == "odin@harness.kit":
        return "odin"
    return "human"


def _compute_efficiency_metrics(cost_data, reflection_data):
    """Compute efficiency-related metrics."""
    total_cache_read = sum(d["cache_read_tokens"] for d in cost_data)
    total_input = sum(d["input_tokens"] for d in cost_data)
    cache_hit_rate = (total_cache_read / total_input * 100) if total_input > 0 else 0

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

    result = []
    for model, info in sorted(models.items(), key=lambda x: -sum(x[1]["costs"]) if x[1]["costs"] else 0):
        avg_cost = (sum(info["costs"]) / len(info["costs"])) if info["costs"] else 0
        avg_duration = (sum(info["durations"]) / len(info["durations"])) if info["durations"] else 0
        avg_tokens = (sum(info["tokens"]) / len(info["tokens"])) if info["tokens"] else 0
        success_rate = (info["success_count"] / info["total_count"] * 100) if info["total_count"] > 0 else 0
        result.append({
            "model": model,
            "avg_cost": round(avg_cost, 4),
            "total_cost": round(sum(info["costs"]), 4) if info["costs"] else 0,
            "avg_duration_ms": round(avg_duration),
            "avg_tokens": round(avg_tokens),
            "success_rate": round(success_rate, 1),
            "task_count": info["total_count"],
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
