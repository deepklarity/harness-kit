"""High-level cost tracker that records task execution costs."""

from typing import Optional

from odin.cost_tracking.estimator import PricingTable, estimate_cost
from odin.cost_tracking.models import TaskCostRecord
from odin.cost_tracking.store import CostStore
from odin.models import TaskResult


class CostTracker:
    """Records cost data after each task execution.

    Extracts duration and token usage from TaskResult and persists
    via CostStore. When a pricing table is provided, estimates cost
    in USD from token counts.
    """

    def __init__(self, store: CostStore, pricing: Optional[PricingTable] = None):
        self._store = store
        self._pricing = pricing

    @property
    def store(self) -> CostStore:
        return self._store

    def record_task(
        self,
        task_id: str,
        spec_id: Optional[str],
        result: TaskResult,
        model: Optional[str] = None,
    ) -> TaskCostRecord:
        """Record cost data from a completed task execution."""
        metadata = result.metadata or {}
        usage = metadata.get("usage", {})

        input_tokens = usage.get("input_tokens") or usage.get("prompt_tokens")
        output_tokens = usage.get("output_tokens") or usage.get("completion_tokens")
        resolved_model = model or metadata.get("model")

        # Estimate cost if pricing table is available
        cost_usd = None
        if self._pricing and resolved_model:
            cost_usd = estimate_cost(resolved_model, input_tokens, output_tokens, self._pricing)

        record = TaskCostRecord(
            task_id=task_id,
            spec_id=spec_id,
            agent=result.agent,
            model=resolved_model,
            duration_ms=result.duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=usage.get("total_tokens"),
            estimated_cost_usd=cost_usd,
            success=result.success,
        )
        self._store.save_record(record)
        return record
