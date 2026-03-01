from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class ProviderState(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class UsageInfo(BaseModel):
    """Usage/quota information for a provider."""
    provider: str
    plan: Optional[str] = None
    quota_limit: Optional[float] = None
    used: Optional[float] = None
    remaining: Optional[float] = None
    usage_pct: Optional[float] = None
    unit: str = "requests"
    reset_date: Optional[datetime] = None
    raw: Optional[dict] = None

    def compute_pct(self) -> Optional[float]:
        if self.quota_limit and self.used:
            self.usage_pct = round((self.used / self.quota_limit) * 100, 1)
        return self.usage_pct


class StatusInfo(BaseModel):
    """Status/health information for a provider."""
    provider: str
    state: ProviderState = ProviderState.UNKNOWN
    latency_ms: Optional[float] = None
    last_checked: Optional[datetime] = None
    message: Optional[str] = None
