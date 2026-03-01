"""Pricing utility — reads model pricing from agent_models.json.

This is the single source of truth for cost computation. The frontend
displays costs; the backend computes them here.
"""

import json
import functools
import re
from pathlib import Path
from typing import Optional

# Matches a trailing date suffix like -20250929 or -20260101
_DATE_SUFFIX_RE = re.compile(r"-\d{8}$")


@functools.lru_cache(maxsize=1)
def get_pricing_table() -> dict:
    """Load pricing data from agent_models.json as a flat dict.

    Returns: {model_name: {input_price_per_1m_tokens, output_price_per_1m_tokens, cache_read_price_per_1m_tokens}}
    Cached per-process (the file doesn't change at runtime).
    """
    path = Path(__file__).resolve().parent.parent / "data" / "agent_models.json"
    data = json.loads(path.read_text())
    table = {}
    for agent_info in data.get("agents", {}).values():
        for model in agent_info.get("models", []):
            table[model["name"]] = {
                "input_price_per_1m_tokens": model.get("input_price_per_1m_tokens"),
                "output_price_per_1m_tokens": model.get("output_price_per_1m_tokens"),
                "cache_read_price_per_1m_tokens": model.get("cache_read_price_per_1m_tokens"),
            }
    return table


def estimate_task_cost(
    model_name: str,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
) -> Optional[float]:
    """Estimate cost in USD for a task execution.

    Returns None if model not found, pricing is null, or tokens are None.
    """
    if input_tokens is None or output_tokens is None:
        return None

    table = get_pricing_table()
    if model_name not in table:
        # Retry without date suffix (e.g. claude-sonnet-4-5-20250929 → claude-sonnet-4-5)
        model_name = _DATE_SUFFIX_RE.sub("", model_name)
        if model_name not in table:
            return None

    entry = table[model_name]
    input_price = entry["input_price_per_1m_tokens"]
    output_price = entry["output_price_per_1m_tokens"]

    if input_price is None or output_price is None:
        return None

    return (input_tokens / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price


def compute_task_estimated_cost(task, usage: dict | None = None) -> Optional[float]:
    """Compute estimated cost for a single task from its trace comment.

    Reads model_name from the task and computes usage on-the-fly from the
    trace comment (source of truth). Returns USD cost or None.

    Args:
        task: Task instance.
        usage: Pre-computed usage dict. If None, will be computed from trace.
    """
    if usage is None:
        from .execution_processing import compute_usage_from_trace
        usage = compute_usage_from_trace(task)
    if not usage:
        return None
    md = task.metadata or {}
    model = task.model_name or md.get("selected_model") or md.get("model")
    if not model:
        return None
    return estimate_task_cost(model, usage.get("input_tokens"), usage.get("output_tokens"))


def compute_spec_cost_summary(tasks, usage_by_task: dict | None = None) -> dict:
    """Aggregate cost summary across a spec's tasks and their reflections.

    Args:
        tasks: iterable of Task objects (queryset or list).
        usage_by_task: optional {task_id: usage_dict} to avoid N+1 queries.

    Returns dict with: total_cost_usd, cost_by_model, total_tokens,
    total_input_tokens, total_output_tokens, tokens_by_model,
    total_duration_ms, tasks_with_unknown_cost, reflection_cost_usd.
    """
    from .execution_processing import compute_usage_from_trace
    from .models import ReflectionReport

    total_cost = 0.0
    total_tokens = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_duration_ms = 0.0
    cost_by_model = {}
    tokens_by_model = {}
    tasks_with_unknown_cost = 0

    task_ids = []
    for task in tasks:
        task_ids.append(task.id)
        md = task.metadata or {}
        if usage_by_task is not None:
            usage = usage_by_task.get(task.id, {})
        else:
            usage = compute_usage_from_trace(task)
        model = task.model_name or md.get("selected_model") or md.get("model")

        # Accumulate tokens
        task_tokens = usage.get("total_tokens") or 0
        total_tokens += task_tokens
        input_t = usage.get("input_tokens")
        output_t = usage.get("output_tokens")
        if input_t:
            total_input_tokens += input_t
        if output_t:
            total_output_tokens += output_t
        if model and task_tokens:
            tokens_by_model[model] = tokens_by_model.get(model, 0) + task_tokens

        # Accumulate duration
        duration = md.get("last_duration_ms")
        if duration:
            total_duration_ms += duration

        # Estimate cost
        cost = estimate_task_cost(model, input_t, output_t) if model else None
        if cost is not None:
            total_cost += cost
            cost_by_model[model] = cost_by_model.get(model, 0) + cost
        else:
            if usage or model:
                tasks_with_unknown_cost += 1

    # Aggregate reflection costs for all tasks in this spec
    reflection_cost = 0.0
    if task_ids:
        reflections = ReflectionReport.objects.filter(
            task_id__in=task_ids,
            status="COMPLETED",
        ).values_list("reviewer_model", "token_usage")
        for reviewer_model, token_usage in reflections:
            usage = token_usage or {}
            r_input = usage.get("input_tokens")
            r_output = usage.get("output_tokens")
            cost = estimate_task_cost(reviewer_model, r_input, r_output) if reviewer_model else None
            if cost is not None:
                reflection_cost += cost

    return {
        "total_cost_usd": round(total_cost, 6),
        "reflection_cost_usd": round(reflection_cost, 6),
        "cost_by_model": {k: round(v, 6) for k, v in cost_by_model.items()},
        "total_tokens": total_tokens,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "tokens_by_model": tokens_by_model,
        "total_duration_ms": round(total_duration_ms, 1),
        "tasks_with_unknown_cost": tasks_with_unknown_cost,
    }
