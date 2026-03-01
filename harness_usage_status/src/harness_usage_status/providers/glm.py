import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from harness_usage_status.models import UsageInfo, StatusInfo, ProviderState
from harness_usage_status.providers.base import BaseProvider
from harness_usage_status.providers.registry import register_provider


@register_provider("glm")
class GLMProvider(BaseProvider):
    """GLM (Zhipu AI / Z.AI) provider.

    Uses the monitor/usage endpoints discovered by community tools:
      GET {base}/api/monitor/usage/quota/limit    — quota limits (5h cycle)
      GET {base}/api/monitor/usage/model-usage     — per-model usage (24h window)
      GET {base}/api/monitor/usage/tool-usage      — MCP tool usage (24h window)

    IMPORTANT: These endpoints use Authorization WITHOUT "Bearer" prefix.

    Also has official Product Billing API at:
      https://open.bigmodel.cn/dev/api/product-billing

    Config extras:
      platform: "cn" (default) or "global"

    Refs:
      - https://github.com/guyinwonder168/opencode-glm-quota
      - https://github.com/vbgate/opencode-mystatus
      - https://github.com/Safphere/glm-usage-jetbrains
    """

    PLATFORM_HOSTS = {
        "cn": "https://bigmodel.cn",
        "global": "https://api.z.ai",
    }

    CHAT_API_HOSTS = {
        "cn": "https://open.bigmodel.cn/api/paas/v4",
        "global": "https://api.z.ai/api/paas/v4",
    }

    @property
    def name(self) -> str:
        return "GLM (Zhipu AI)"

    def _platform(self) -> str:
        return self._extras().get("platform", "cn")

    def _monitor_host(self) -> str:
        if self.config.get("base_url"):
            return self.config["base_url"].rstrip("/")
        return self.PLATFORM_HOSTS.get(self._platform(), self.PLATFORM_HOSTS["cn"])

    def _chat_api_base(self) -> str:
        return self.CHAT_API_HOSTS.get(self._platform(), self.CHAT_API_HOSTS["cn"])

    def _api_key(self) -> Optional[str]:
        return self.config.get("api_key")

    def _extras(self) -> dict:
        return self.config.get("extras", {})

    def _monitor_headers(self) -> dict:
        # NOTE: Monitor endpoints use token WITHOUT "Bearer" prefix
        return {
            "Authorization": self._api_key() or "",
            "Accept": "application/json",
        }

    def _chat_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key() or ''}",
            "Content-Type": "application/json",
        }

    def _safe_json(self, resp: httpx.Response) -> dict:
        """Parse JSON response, returning empty dict on failure."""
        if resp.status_code != 200:
            return {"_http_status": resp.status_code}
        if not resp.content:
            return {}
        try:
            return resp.json()
        except Exception:
            return {"_error": f"HTTP {resp.status_code}, non-JSON response"}

    async def get_usage(self) -> UsageInfo:
        if not self._api_key():
            return UsageInfo(
                provider=self.name,
                raw={"error": "ZAI_API_KEY not configured"},
            )
        host = self._monitor_host()
        async with httpx.AsyncClient() as client:
            # Fetch quota limits
            resp = await client.get(
                f"{host}/api/monitor/usage/quota/limit",
                headers=self._monitor_headers(),
                timeout=15,
            )
            quota_data = self._safe_json(resp)

            # Fetch model usage (24h window) — requires time window params
            now = datetime.now(timezone.utc)
            start_24h = now - timedelta(hours=24)
            time_params = {
                "startTime": start_24h.strftime("%Y-%m-%d %H:%M:%S"),
                "endTime": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
            resp2 = await client.get(
                f"{host}/api/monitor/usage/model-usage",
                headers=self._monitor_headers(),
                params=time_params,
                timeout=15,
            )
            model_data = self._safe_json(resp2) if resp2.content else {}

            # Check for errors in quota response (model-usage is optional)
            if "_error" in quota_data or "_http_status" in quota_data:
                return UsageInfo(
                    provider=self.name,
                    raw={"error": f"quota API: HTTP {quota_data.get('_http_status', '?')}"},
                )

            # Parse quota data
            # API returns two limit types:
            #   TOKENS_LIMIT — actual coding token usage (percentage-based)
            #   TIME_LIMIT   — ancillary tool request counts (currentValue/remaining/usage)
            # We prefer TOKENS_LIMIT as it reflects real coding usage.
            used = None
            remaining = None
            quota_limit = None
            usage_pct = None
            level = None
            unit = "requests"
            data = quota_data.get("data", {})
            level = data.get("level")

            tokens_limits = []
            time_limit = None
            for limit in data.get("limits", []):
                if limit.get("type") == "TOKENS_LIMIT":
                    tokens_limits.append(limit)
                elif limit.get("type") == "TIME_LIMIT":
                    time_limit = limit

            reset_date = None
            if tokens_limits:
                # TOKENS_LIMIT entries have percentage (0.0-1.0) of token budget used.
                # The 5-hour window entry (unit=3) is the most relevant for coding.
                # Pick the entry with unit=3 (5h window) if available, else highest pct.
                five_hour = [t for t in tokens_limits if t.get("unit") == 3]
                primary = five_hour[0] if five_hour else max(
                    tokens_limits, key=lambda x: x.get("percentage", 0)
                )
                # percentage is already on a 0-100 scale
                usage_pct = primary.get("percentage", 0)
                unit = "tokens"
                reset_ts = primary.get("nextResetTime")
                if reset_ts:
                    reset_date = datetime.fromtimestamp(reset_ts / 1000)
            elif time_limit:
                # Fallback to TIME_LIMIT if no TOKENS_LIMIT entries
                used = time_limit.get("currentValue", 0)
                remaining = time_limit.get("remaining")
                quota_limit = time_limit.get("usage")

            return UsageInfo(
                provider=self.name,
                plan=f"Coding Plan ({level})" if level else "Coding Plan",
                used=used,
                remaining=remaining,
                quota_limit=quota_limit,
                usage_pct=usage_pct,
                unit=unit,
                reset_date=reset_date,
                raw={
                    "quota_limit": quota_data,
                    "model_usage": model_data,
                },
            )

    async def get_status(self) -> StatusInfo:
        if not self._api_key():
            return StatusInfo(
                provider=self.name,
                state=ProviderState.UNKNOWN,
                last_checked=datetime.now(),
                message="ZAI_API_KEY not configured",
            )
        start = time.monotonic()
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self._chat_api_base()}/models",
                    headers=self._chat_headers(),
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
