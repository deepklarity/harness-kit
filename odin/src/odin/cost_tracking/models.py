"""Pydantic models for cost tracking."""

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class TaskCostRecord(BaseModel):
    """Cost record for a single task execution."""

    task_id: str
    spec_id: Optional[str] = None
    agent: Optional[str] = None
    model: Optional[str] = None
    duration_ms: Optional[float] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    estimated_cost_usd: Optional[float] = None
    success: bool = True
    recorded_at: datetime = Field(default_factory=datetime.now)


class SpecCostSummary(BaseModel):
    """Aggregated cost summary for a spec."""

    spec_id: str
    total_duration_ms: float = 0.0
    task_count: int = 0
    invocations_by_agent: Dict[str, int] = Field(default_factory=dict)
    total_tokens: int = 0
    tokens_by_agent: Dict[str, int] = Field(default_factory=dict)
    total_estimated_cost_usd: Optional[float] = None
    cost_by_agent: Dict[str, float] = Field(default_factory=dict)
    first_recorded: Optional[datetime] = None
    last_recorded: Optional[datetime] = None
