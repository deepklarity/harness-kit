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
def _agent_registry() -> dict:
    """The full agents section of agent_models.json — including retired
    agents/models kept for historical cost lookups (each flagged
    ``"retired": true``). This is NOT the active lineup by itself; consult
    get_active_agents() for that. Anything routing or defaulting
    agents/models must consult this module, never DB copies (seedmodels
    merges but does not prune) and never hardcoded lists (F45)."""
    path = Path(__file__).resolve().parent.parent / "data" / "agent_models.json"
    return json.loads(path.read_text()).get("agents", {})


def get_active_agents() -> frozenset:
    """Names of active/supported agents per agent_models.json.

    Excludes agents flagged ``"retired": true`` — retired agents remain in
    the registry (so historical tasks still price) but must never surface
    in routing/UI as selectable.
    """
    return frozenset(
        name for name, info in _agent_registry().items()
        if not info.get("retired")
    )


def get_agent_default_model(agent_name: str) -> Optional[str]:
    """Default model for an active agent per agent_models.json.

    Resolution: model flagged is_default → agent's default_model key →
    first listed model. None for unknown/retired agents or agents whose
    only models are retired.
    """
    info = _agent_registry().get((agent_name or "").lower())
    if not info or info.get("retired"):
        return None
    active_models = [m for m in info.get("models", []) if not m.get("retired")]
    for model in active_models:
        if model.get("is_default") and model.get("name"):
            return model["name"]
    if info.get("default_model"):
        return info["default_model"]
    return active_models[0].get("name") if active_models else None


@functools.lru_cache(maxsize=1)
def get_agent_cost_tiers() -> dict:
    """Load each agent's cost_tier from agent_models.json.

    Returns: {agent_name: cost_tier} e.g. {"claude": "high", "glm": "low"}.
    Cached per-process (the file doesn't change at runtime).
    """
    path = Path(__file__).resolve().parent.parent / "data" / "agent_models.json"
    data = json.loads(path.read_text())
    return {
        name: agent_info.get("cost_tier")
        for name, agent_info in data.get("agents", {}).items()
    }


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


def compute_spec_cost_summary(tasks, usage_by_task: dict | None = None, spec=None) -> dict:
    """Aggregate cost summary across a spec's tasks — all four categories.

    Sums Plan / Build / Review / Merge costs from a single source of truth:
    the same ``estimate_task_cost`` pricing function applied to each
    category's token-usage records.

    Args:
        tasks: iterable of Task objects (queryset or list).
        usage_by_task: optional {task_id: usage_dict} to avoid N+1 queries.
        spec: optional Spec instance — when provided, plan cost is derived
            from ``spec.metadata['planning_trace']['token_usage']``.

    Returns dict with: plan_cost_usd, total_cost_usd (build),
    reflection_cost_usd (review), merge_cost_usd, cost_by_model,
    total_tokens, total_input_tokens, total_output_tokens,
    tokens_by_model, total_duration_ms, tasks_with_unknown_cost.
    """
    from .execution_processing import compute_usage_from_trace
    from .models import MergeAttempt, ReflectionReport

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

    # ── Review cost: sum completed reflections ──────────────────────
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

    # ── Merge cost: sum MergeAttempt rows ───────────────────────────
    merge_cost = 0.0
    if task_ids:
        for agent_model, token_usage in MergeAttempt.objects.filter(
            task_id__in=task_ids,
        ).values_list("agent_model", "token_usage"):
            usage = token_usage or {}
            m_input = usage.get("input_tokens")
            m_output = usage.get("output_tokens")
            cost = estimate_task_cost(agent_model, m_input, m_output) if agent_model else None
            if cost is not None:
                merge_cost += cost

    # ── Plan cost: from spec.metadata['planning_trace'] ─────────────
    plan_cost = 0.0
    if spec is not None:
        spec_meta = (spec.metadata or {}).get("planning_trace") or {}
        plan_usage = spec_meta.get("token_usage") or {}
        plan_model = spec_meta.get("model")
        if plan_model and plan_usage:
            cost = estimate_task_cost(
                plan_model,
                plan_usage.get("input_tokens"),
                plan_usage.get("output_tokens"),
            )
            if cost is not None:
                plan_cost = cost

    return {
        "plan_cost_usd": round(plan_cost, 6),
        "total_cost_usd": round(total_cost, 6),
        "reflection_cost_usd": round(reflection_cost, 6),
        "merge_cost_usd": round(merge_cost, 6),
        "cost_by_model": {k: round(v, 6) for k, v in cost_by_model.items()},
        "total_tokens": total_tokens,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "tokens_by_model": tokens_by_model,
        "total_duration_ms": round(total_duration_ms, 1),
        "tasks_with_unknown_cost": tasks_with_unknown_cost,
    }


def compute_spec_merge_summary(tasks) -> dict:
    """Aggregate merge-attempt rollup across a spec's tasks (task #209).

    Rolls up MergeAttempt rows the same way ``compute_spec_cost_summary``
    rolls up ReflectionReport rows: one query, cost derived on read via
    ``estimate_task_cost`` (never stored on the row).

    Returns dict with: attempt_count, static_count, agent_count,
    human_assisted_count, merge_cost_usd, mean_dispatch_lag_seconds
    (None if no attempt captured a dispatch lag), conflicts_by_file.
    """
    from .models import MergeAttempt, MergeMode

    task_ids = [t.id for t in tasks]
    attempts = list(MergeAttempt.objects.filter(task_id__in=task_ids)) if task_ids else []

    mode_counts = {MergeMode.STATIC: 0, MergeMode.AGENT: 0, MergeMode.HUMAN_ASSISTED: 0}
    merge_cost = 0.0
    lags = []
    conflicts_by_file = {}

    for attempt in attempts:
        mode_counts[attempt.mode] = mode_counts.get(attempt.mode, 0) + 1

        if attempt.agent_model:
            usage = attempt.token_usage or {}
            cost = estimate_task_cost(attempt.agent_model, usage.get("input_tokens"), usage.get("output_tokens"))
            if cost is not None:
                merge_cost += cost

        if attempt.dispatched_at and attempt.started_at:
            lags.append((attempt.started_at - attempt.dispatched_at).total_seconds())

        for path in attempt.conflicting_files or []:
            conflicts_by_file[path] = conflicts_by_file.get(path, 0) + 1

    return {
        "attempt_count": len(attempts),
        "static_count": mode_counts[MergeMode.STATIC],
        "agent_count": mode_counts[MergeMode.AGENT],
        "human_assisted_count": mode_counts[MergeMode.HUMAN_ASSISTED],
        "merge_cost_usd": round(merge_cost, 6),
        "mean_dispatch_lag_seconds": round(sum(lags) / len(lags), 3) if lags else None,
        "conflicts_by_file": conflicts_by_file,
    }
