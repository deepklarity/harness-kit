import time
from datetime import datetime
from typing import Optional

from harness_usage_status.cli_runner import run_cli, find_cli
from harness_usage_status.models import UsageInfo, StatusInfo, ProviderState
from harness_usage_status.providers.base import BaseProvider
from harness_usage_status.providers.registry import register_provider


@register_provider("qwen")
class QwenProvider(BaseProvider):
    """Qwen Code CLI subscription provider.

    Uses the `qwen` CLI to get usage stats.
    The CLI has `/stats` and `/stats model` commands for session-level usage.

    No account-level quota API exists for DashScope.
    FIXME: Browser automation needed for account-level quota from console.

    Config extras:
      cli_path: path to qwen binary (default: auto-detect from PATH)
      cli_command: command to run (default: "qwen")
      region: "intl" (default), "us", or "cn" (for API fallback)

    Refs:
      - https://github.com/QwenLM/qwen-code
      - Qwen Code /stats is session-scoped only
    """

    @property
    def name(self) -> str:
        return "Qwen"

    def _cli(self) -> str:
        extras = self.config.get("extras", {})
        return extras.get("cli_path") or extras.get("cli_command") or "qwen"

    async def get_usage(self) -> UsageInfo:
        cli = self._cli()
        if not find_cli(cli):
            return UsageInfo(
                provider=self.name,
                raw={"error": f"CLI '{cli}' not found on PATH"},
            )

        # Try `qwen usage` or similar non-interactive command
        result = await run_cli([cli, "usage"], timeout=15)

        if not result.ok:
            result = await run_cli([cli, "--help"], timeout=10)
            return UsageInfo(
                provider=self.name,
                raw={
                    "error": "Could not fetch usage stats",
                    "stderr": result.stderr[:500],
                    "stdout": result.stdout[:500],
                    "note": "FIXME: Qwen Code /stats is interactive/session-scoped. No account-level quota API.",
                },
            )

        return UsageInfo(
            provider=self.name,
            plan="Subscription",
            raw={"output": result.stdout},
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
