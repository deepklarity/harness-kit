import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from harness_usage_status.models import UsageInfo, StatusInfo, ProviderState
from harness_usage_status.providers.base import BaseProvider
from harness_usage_status.providers.registry import register_provider


def _read_opencode_auth_token() -> Optional[str]:
    """Read MiniMax API key from ~/.local/share/opencode/auth.json.

    Checks keys in order: "minimax-coding-plan", "minimax".
    """
    auth_path = Path.home() / ".local" / "share" / "opencode" / "auth.json"
    try:
        with open(auth_path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    for key in ("minimax-coding-plan", "minimax"):
        entry = data.get(key)
        if isinstance(entry, dict) and entry.get("key"):
            return entry["key"]
    return None


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
        # 1. Config / MINIMAX_API_KEY env var (resolved by config loader)
        key = self.config.get("api_key")
        if key:
            return key
        # 2. Fallback: ~/.local/share/opencode/auth.json
        return _read_opencode_auth_token()

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
                raw={"error": "MiniMax not configured (set MINIMAX_API_KEY or add key to ~/.local/share/opencode/auth.json)"},
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
            # REMAINING count (the endpoint is called "remains"). Confirmed
            # against the MiniMax-Coding-Plan-MCP / CodexBar reference
            # parsers, which map current_interval_usage_count -> remaining.
            #
            # Models not on the account's plan report
            # current_interval_total_count == 0 (no quota allocated at all).
            # Summing those in would be harmless to the aggregate (0
            # contributes nothing), but we still skip them explicitly so the
            # aggregate is built only from models actually on the plan —
            # this also keeps get_usage() and the "skip 0-limit models" rule
            # consistent with how the frontend must render per-model rows.
            model_remains = data.get("model_remains", [])
            in_plan_models = [
                m for m in model_remains
                if (m.get("current_interval_total_count") or 0) > 0
            ]
            if not in_plan_models:
                # No model on this account currently has a real quota
                # allocation. Surface as "no data" rather than a
                # misleading 0/0 (the reported bug) — 0/0 reads as
                # "quota exhausted" when it actually means "nothing to
                # report".
                # Count-based quota fields are all zero on this plan shape,
                # but the API still reports percentage-based windows:
                # current_interval_remaining_percent per model. 5 means 5%
                # LEFT (95% used). Use the busiest model's window — that is
                # the number a human needs to see before a run dies mid-wave.
                pct_models = [
                    m for m in model_remains
                    if m.get("current_interval_remaining_percent") is not None
                ]
                if pct_models:
                    busiest = min(
                        pct_models,
                        key=lambda m: m.get("current_interval_remaining_percent", 100),
                    )
                    remaining = float(busiest.get("current_interval_remaining_percent", 100))
                    return UsageInfo(
                        provider=self.name,
                        plan="Coding Plan",
                        unit="%",
                        usage_pct=round(100.0 - remaining, 1),
                        raw=data,
                    )
                # Truly nothing to report — say so rather than a misleading 0/0.
                return UsageInfo(
                    provider=self.name,
                    plan="Coding Plan",
                    unit="prompts",
                    raw={**data, "error": "MiniMax reported no quota windows for this account"},
                )

            total_remaining = sum(
                m.get("current_interval_usage_count", 0) for m in in_plan_models
            )
            total_limit = sum(
                m.get("current_interval_total_count", 0) for m in in_plan_models
            )
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
                message="MiniMax not configured (set MINIMAX_API_KEY or add key to ~/.local/share/opencode/auth.json)",
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
