import json
import os
import platform
import subprocess
import time
from datetime import datetime
from typing import Optional

import httpx

from harness_usage_status.cli_runner import run_cli, find_cli
from harness_usage_status.models import UsageInfo, StatusInfo, ProviderState
from harness_usage_status.providers.base import BaseProvider
from harness_usage_status.providers.registry import register_provider


def _get_oauth_token() -> Optional[str]:
    """Extract Claude Code OAuth access token from local credentials.

    Checks in order:
      1. CLAUDE_CODE_OAUTH_TOKEN env var
      2. macOS Keychain (Claude Code-credentials)
      3. ~/.claude/.credentials.json (Linux / headless)
    """
    # 1. Env var override
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if token:
        return token

    # 2. macOS Keychain
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                creds = json.loads(result.stdout.strip())
                oauth = creds.get("claudeAiOauth", {})
                access_token = oauth.get("accessToken")
                if access_token:
                    return access_token
        except (json.JSONDecodeError, subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # 3. Credentials file (Linux / headless / fallback)
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude"))
    creds_path = os.path.join(config_dir, ".credentials.json")
    try:
        with open(creds_path) as f:
            creds = json.load(f)
        oauth = creds.get("claudeAiOauth", {})
        return oauth.get("accessToken")
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass

    return None


@register_provider("claude_code")
class ClaudeCodeProvider(BaseProvider):
    """Claude Code subscription provider.

    Fetches usage data from the Anthropic OAuth usage API:
      GET https://api.anthropic.com/api/oauth/usage

    Auth token is read from:
      - CLAUDE_CODE_OAUTH_TOKEN env var
      - macOS Keychain "Claude Code-credentials" → claudeAiOauth.accessToken
      - ~/.claude/.credentials.json → claudeAiOauth.accessToken

    Response contains five_hour and seven_day utilization percentages.

    Config extras:
      cli_path: path to claude binary (for status check)
      cli_command: command name (default: "claude")

    Refs:
      - https://codelynx.dev/posts/claude-code-usage-limits-statusline
      - https://gist.github.com/patyearone/7c753ef536a49839c400efaf640e17de
    """

    USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

    @property
    def name(self) -> str:
        return "Claude Code"

    def _cli(self) -> str:
        extras = self.config.get("extras", {})
        return extras.get("cli_path") or extras.get("cli_command") or "claude"

    async def get_usage(self) -> UsageInfo:
        token = _get_oauth_token()
        if not token:
            return UsageInfo(
                provider=self.name,
                raw={"error": "No OAuth token found (check Keychain or set CLAUDE_CODE_OAUTH_TOKEN)"},
            )

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    self.USAGE_URL,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "anthropic-beta": "oauth-2025-04-20",
                        "User-Agent": "harness-usage-status/0.1",
                        "Accept": "application/json",
                    },
                    timeout=15,
                )
                if resp.status_code != 200:
                    return UsageInfo(
                        provider=self.name,
                        raw={"error": f"API returned {resp.status_code}", "body": resp.text[:500]},
                    )

                data = resp.json()

                # Parse five_hour and seven_day utilization
                five_hour = data.get("five_hour") or {}
                seven_day = data.get("seven_day") or {}

                five_hour_pct = five_hour.get("utilization", 0)
                seven_day_pct = seven_day.get("utilization", 0)
                five_hour_reset = five_hour.get("resets_at")
                seven_day_reset = seven_day.get("resets_at")

                # Use the higher of the two as the "usage_pct"
                usage_pct = max(five_hour_pct, seven_day_pct)

                return UsageInfo(
                    provider=self.name,
                    plan="Subscription",
                    usage_pct=usage_pct,
                    used=usage_pct,
                    remaining=round(100 - usage_pct, 1),
                    quota_limit=100,
                    unit="%",
                    raw={
                        "five_hour": {"utilization": five_hour_pct, "resets_at": five_hour_reset},
                        "seven_day": {"utilization": seven_day_pct, "resets_at": seven_day_reset},
                        **{k: v for k, v in data.items() if k not in ("five_hour", "seven_day")},
                    },
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
