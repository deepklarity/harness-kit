import os
import time
from datetime import datetime
from typing import Optional

import httpx

from harness_usage_status.models import UsageInfo, StatusInfo, ProviderState
from harness_usage_status.providers.base import BaseProvider
from harness_usage_status.providers.registry import register_provider


@register_provider("minimax")
class MiniMaxProvider(BaseProvider):
    """MiniMax AI provider.

    Uses the official Coding Plan remains endpoint (no group_id needed):
      GET https://www.minimax.io/v1/api/openplatform/coding_plan/remains

    The sk-cp-... Coding Plan key authenticates directly via Bearer token.

    Config extras:
      region: "global" (default) or "cn"

    For China mainland accounts, the endpoint is:
      GET https://api.minimaxi.com/v1/api/openplatform/coding_plan/remains

    Refs:
      - https://platform.minimax.io/docs/coding-plan/faq (official curl example)
      - https://github.com/MiniMax-AI/MiniMax-Coding-Plan-MCP
      - https://github.com/steipete/CodexBar
    """

    # Official API hosts per region
    REGION_HOSTS = {
        "global": "https://api.minimax.io",
        "cn": "https://api.minimaxi.com",
    }

    REMAINS_PATH = "/v1/api/openplatform/coding_plan/remains"

    @property
    def name(self) -> str:
        return "MiniMax"

    def _host(self) -> str:
        if self.config.get("base_url"):
            return self.config["base_url"].rstrip("/")
        region = self._extras().get("region", "global")
        return self.REGION_HOSTS.get(region, self.REGION_HOSTS["global"])

    def _api_key(self) -> Optional[str]:
        return self.config.get("api_key")

    def _extras(self) -> dict:
        return self.config.get("extras", {})

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key() or ''}",
            "Content-Type": "application/json",
        }

    async def get_usage(self) -> UsageInfo:
        if not self._api_key():
            return UsageInfo(
                provider=self.name,
                raw={"error": "MINIMAX_API_KEY not configured"},
            )

        url = f"{self._host()}{self.REMAINS_PATH}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                headers=self._headers(),
                timeout=15,
            )
            if resp.status_code != 200:
                return UsageInfo(
                    provider=self.name,
                    raw={"error": f"HTTP {resp.status_code} from MiniMax API"},
                )
            try:
                data = resp.json()
            except Exception:
                return UsageInfo(
                    provider=self.name,
                    raw={"error": "Non-JSON response from MiniMax API"},
                )

            # Check for API-level errors
            base_resp = data.get("base_resp", {})
            if base_resp.get("status_code", 0) != 0:
                return UsageInfo(
                    provider=self.name,
                    raw={"error": base_resp.get("status_msg", "Unknown API error")},
                )

            # Aggregate across all models in model_remains
            # NOTE: despite the name, current_interval_usage_count is the
            # REMAINING count (the endpoint is called "remains")
            model_remains = data.get("model_remains", [])
            total_remaining = 0
            total_limit = 0
            for m in model_remains:
                total_remaining += m.get("current_interval_usage_count", 0)
                total_limit += m.get("current_interval_total_count", 0)
            total_used = total_limit - total_remaining

            return UsageInfo(
                provider=self.name,
                plan="Coding Plan",
                used=total_used,
                remaining=total_remaining,
                quota_limit=total_limit,
                unit="prompts",
                raw=data,
            )

    async def get_status(self) -> StatusInfo:
        if not self._api_key():
            return StatusInfo(
                provider=self.name,
                state=ProviderState.UNKNOWN,
                last_checked=datetime.now(),
                message="MINIMAX_API_KEY not configured",
            )
        start = time.monotonic()
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self._host()}{self.REMAINS_PATH}",
                    headers=self._headers(),
                    timeout=10,
                )
                latency = (time.monotonic() - start) * 1000
                state = ProviderState.ONLINE if resp.status_code == 200 else ProviderState.DEGRADED
                return StatusInfo(
                    provider=self.name,
                    state=state,
                    latency_ms=round(latency, 1),
                    last_checked=datetime.now(),
                )
        except Exception as e:
            return StatusInfo(
                provider=self.name,
                state=ProviderState.OFFLINE,
                last_checked=datetime.now(),
                message=str(e),
            )
