import json
import os
import time
from datetime import datetime
from typing import Optional

import httpx

from harness_usage_status.cli_runner import run_cli, find_cli
from harness_usage_status.models import UsageInfo, StatusInfo, ProviderState
from harness_usage_status.providers.base import BaseProvider
from harness_usage_status.providers.registry import register_provider


def _get_codex_token() -> Optional[str]:
    """Extract Codex access token from ~/.codex/auth.json.

    File format:
      { "tokens": { "access_token": "...", "refresh_token": "...", ... } }
    """
    token = os.environ.get("CODEX_ACCESS_TOKEN")
    if token:
        return token

    auth_path = os.path.expanduser("~/.codex/auth.json")
    try:
        with open(auth_path) as f:
            data = json.load(f)
        tokens = data.get("tokens", {})
        return tokens.get("access_token")
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass

    return None


@register_provider("codex")
class CodexProvider(BaseProvider):
    """Codex (OpenAI) subscription provider.

    Fetches usage data from the ChatGPT WHAM usage endpoint:
      GET https://chatgpt.com/backend-api/wham/usage

    Auth token is read from:
      - CODEX_ACCESS_TOKEN env var
      - ~/.codex/auth.json → tokens.access_token

    Also supports the Codex app-server JSON-RPC method
    account/rateLimits/read which returns:
      { "rateLimits": { "primary": { "usedPercent": 25, "windowDurationMins": 15, "resetsAt": unix_ts } } }

    Config extras:
      cli_path: path to codex binary (for status check)
      cli_command: command name (default: "codex")

    Refs:
      - https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md
      - https://github.com/steipete/CodexBar/blob/main/docs/codex.md
    """

    WHAM_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"

    @property
    def name(self) -> str:
        return "Codex (OpenAI)"

    def _cli(self) -> str:
        extras = self.config.get("extras", {})
        return extras.get("cli_path") or extras.get("cli_command") or "codex"

    async def get_usage(self) -> UsageInfo:
        token = _get_codex_token()
        if not token:
            return UsageInfo(
                provider=self.name,
                raw={"error": "No Codex token found (check ~/.codex/auth.json or set CODEX_ACCESS_TOKEN)"},
            )

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    self.WHAM_USAGE_URL,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "User-Agent": "harness-usage-status/0.1",
                    },
                    timeout=15,
                )
                if resp.status_code != 200:
                    return UsageInfo(
                        provider=self.name,
                        raw={"error": f"API returned {resp.status_code}", "body": resp.text[:500]},
                    )

                data = resp.json()

                # WHAM endpoint returns rate_limit with primary_window and secondary_window
                rate_limit = data.get("rate_limit", {})
                primary = rate_limit.get("primary_window", {})
                secondary = rate_limit.get("secondary_window", {})
                plan_type = data.get("plan_type", "unknown")

                if primary or secondary:
                    primary_pct = primary.get("used_percent", 0) if primary else 0
                    secondary_pct = secondary.get("used_percent", 0) if secondary else 0
                    usage_pct = max(primary_pct, secondary_pct)

                    # Build raw with structured info
                    raw = {
                        "primary_window": primary,
                        "secondary_window": secondary,
                        "plan_type": plan_type,
                        "email": data.get("email"),
                    }

                    return UsageInfo(
                        provider=self.name,
                        plan=f"{plan_type.title()} Plan",
                        usage_pct=usage_pct,
                        used=usage_pct,
                        remaining=round(100 - usage_pct, 1),
                        quota_limit=100,
                        unit="%",
                        raw=raw,
                    )

                # Fallback for other response formats (app-server RPC)
                rate_limits = data.get("rateLimits", {})
                rpc_primary = rate_limits.get("primary", {}) if isinstance(rate_limits, dict) else {}
                if rpc_primary:
                    used_pct = rpc_primary.get("usedPercent", 0)
                    return UsageInfo(
                        provider=self.name,
                        plan="Subscription",
                        usage_pct=used_pct,
                        used=used_pct,
                        remaining=round(100 - used_pct, 1),
                        quota_limit=100,
                        unit="%",
                        raw=data,
                    )

                # Final fallback: return raw data
                return UsageInfo(
                    provider=self.name,
                    plan="Subscription",
                    raw=data,
                )
        except Exception as e:
            return UsageInfo(
                provider=self.name,
                raw={"error": str(e)},
            )

    async def get_status(self) -> StatusInfo:
        cli = self._cli()
        start = time.monotonic()
        if not find_cli(cli):
            return StatusInfo(
                provider=self.name,
                state=ProviderState.OFFLINE,
                last_checked=datetime.now(),
                message=f"CLI '{cli}' not found on PATH",
            )

        result = await run_cli([cli, "--version"], timeout=10)
        latency = (time.monotonic() - start) * 1000
        state = ProviderState.ONLINE if result.ok else ProviderState.DEGRADED

        return StatusInfo(
            provider=self.name,
            state=state,
            latency_ms=round(latency, 1),
            last_checked=datetime.now(),
            message=result.stdout.strip() if result.ok else result.stderr[:200],
        )
