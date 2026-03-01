"""Disk-based cost record storage."""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from odin.cost_tracking.models import SpecCostSummary, TaskCostRecord


class CostStore:
    """Store and retrieve cost records on disk.

    Follows the existing JSON-on-disk pattern (.odin/costs/).
    Per-spec file: costs_{spec_id}.json containing a list of TaskCostRecord.
    """

    def __init__(self, storage_dir: str = ".odin/costs"):
        self._dir = Path(storage_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _spec_path(self, spec_id: str) -> Path:
        return self._dir / f"costs_{spec_id}.json"

    def save_record(self, record: TaskCostRecord) -> None:
        """Append a cost record for a spec."""
        spec_id = record.spec_id or "_orphan"
        path = self._spec_path(spec_id)
        records = self._load_raw(path)
        records.append(record.model_dump(mode="json"))
        path.write_text(json.dumps(records, indent=2, default=str))

    def load_by_spec(self, spec_id: str) -> List[TaskCostRecord]:
        """Load all cost records for a given spec."""
        path = self._spec_path(spec_id)
        return [TaskCostRecord(**r) for r in self._load_raw(path)]

    def load_all(self) -> List[TaskCostRecord]:
        """Load all cost records across all specs."""
        records = []
        for path in sorted(self._dir.glob("costs_*.json")):
            for r in self._load_raw(path):
                records.append(TaskCostRecord(**r))
        return records

    def summarize_spec(self, spec_id: str) -> SpecCostSummary:
        """Build a cost summary for a single spec."""
        return self._summarize(spec_id, self.load_by_spec(spec_id))

    def summarize_all(self) -> List[SpecCostSummary]:
        """Build cost summaries for all specs."""
        by_spec: Dict[str, List[TaskCostRecord]] = {}
        for record in self.load_all():
            sid = record.spec_id or "_orphan"
            by_spec.setdefault(sid, []).append(record)
        return [self._summarize(sid, recs) for sid, recs in by_spec.items()]

    def summarize_task(self, spec_id: str, task_id: str) -> dict:
        """Summarize a single task across all its invocations (retries).

        Returns a dict with total_duration_ms, total_tokens, input_tokens,
        output_tokens, estimated_cost_usd, invocation_count.
        """
        records = [r for r in self.load_by_spec(spec_id) if r.task_id == task_id]
        total_duration = 0.0
        total_tokens = 0
        input_tokens = 0
        output_tokens = 0
        total_cost = 0.0
        has_cost = False

        for r in records:
            if r.duration_ms:
                total_duration += r.duration_ms
            if r.total_tokens:
                total_tokens += r.total_tokens
            if r.input_tokens:
                input_tokens += r.input_tokens
            if r.output_tokens:
                output_tokens += r.output_tokens
            if r.estimated_cost_usd is not None:
                total_cost += r.estimated_cost_usd
                has_cost = True

        return {
            "task_id": task_id,
            "total_duration_ms": round(total_duration, 1),
            "total_tokens": total_tokens,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_usd": round(total_cost, 6) if has_cost else None,
            "invocation_count": len(records),
        }

    @staticmethod
    def _summarize(spec_id: str, records: List[TaskCostRecord]) -> SpecCostSummary:
        total_duration = 0.0
        total_tokens = 0
        invocations: Dict[str, int] = {}
        tokens_by_agent: Dict[str, int] = {}
        cost_by_agent: Dict[str, float] = {}
        total_cost = 0.0
        has_cost = False
        first: Optional[datetime] = None
        last: Optional[datetime] = None

        for r in records:
            if r.duration_ms:
                total_duration += r.duration_ms
            if r.total_tokens:
                total_tokens += r.total_tokens
            agent = r.agent or "unknown"
            invocations[agent] = invocations.get(agent, 0) + 1
            if r.total_tokens:
                tokens_by_agent[agent] = tokens_by_agent.get(agent, 0) + r.total_tokens
            if r.estimated_cost_usd is not None:
                cost_by_agent[agent] = cost_by_agent.get(agent, 0) + r.estimated_cost_usd
                total_cost += r.estimated_cost_usd
                has_cost = True
            if first is None or r.recorded_at < first:
                first = r.recorded_at
            if last is None or r.recorded_at > last:
                last = r.recorded_at

        return SpecCostSummary(
            spec_id=spec_id,
            total_duration_ms=round(total_duration, 1),
            task_count=len(records),
            invocations_by_agent=invocations,
            total_tokens=total_tokens,
            tokens_by_agent=tokens_by_agent,
            total_estimated_cost_usd=round(total_cost, 6) if has_cost else None,
            cost_by_agent=cost_by_agent,
            first_recorded=first,
            last_recorded=last,
        )

    @staticmethod
    def _load_raw(path: Path) -> list:
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return []
