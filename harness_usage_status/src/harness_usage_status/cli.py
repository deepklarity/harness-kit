import asyncio
import json
from typing import Optional

import fire
from rich.console import Console
from rich.table import Table

from harness_usage_status.config import load_config
from harness_usage_status.providers import get_all_providers

console = Console()


class HarnessUsageStatus:
    """Centralized usage quota and status viewer for AI model providers."""

    def __init__(self, config: Optional[str] = None):
        """
        Args:
            config: Path to config YAML file. Defaults to ~/.config/harness_usage_status/config.yaml
        """
        self._config_path = config
        self._app_config = load_config(config)

    def _get_providers(self, provider: Optional[str] = None):
        """Get provider instances, optionally filtered to a single one."""
        configs = self._app_config.get_provider_configs()
        if provider:
            if provider not in configs:
                console.print(f"[red]Unknown provider: {provider}[/red]")
                console.print(f"Available: {', '.join(configs.keys())}")
                return {}
            configs = {provider: configs[provider]}
        return get_all_providers(configs)

    def quota(self, provider: Optional[str] = None, output: str = "table"):
        """Show usage quota for all configured (or a single) provider.

        Args:
            provider: Query a single provider by name (e.g. 'claude_code')
            output: Output format — 'table' (default) or 'json'
        """
        providers = self._get_providers(provider)
        if not providers:
            console.print("[yellow]No providers configured or enabled.[/yellow]")
            return

        results = asyncio.run(self._fetch_all_usage(providers))

        if output == "json":
            data = [r.model_dump(mode="json") for r in results]
            console.print(json.dumps(data, indent=2, default=str))
        else:
            self._render_quota_table(results)

    def status(self, provider: Optional[str] = None, output: str = "table"):
        """Show status/health for all configured (or a single) provider.

        Args:
            provider: Query a single provider by name (e.g. 'claude_code')
            output: Output format — 'table' (default) or 'json'
        """
        providers = self._get_providers(provider)
        if not providers:
            console.print("[yellow]No providers configured or enabled.[/yellow]")
            return

        results = asyncio.run(self._fetch_all_status(providers))

        if output == "json":
            data = [r.model_dump(mode="json") for r in results]
            console.print(json.dumps(data, indent=2, default=str))
        else:
            self._render_status_table(results)

    def config(self):
        """Show current configuration, config source, and which providers are enabled."""
        source = self._app_config.config_source or "unknown"
        console.print(f"[dim]Config loaded from:[/dim] {source}\n")

        table = Table(title="Provider Configuration")
        table.add_column("Provider", style="cyan")
        table.add_column("Enabled", style="green")
        table.add_column("API Key", style="yellow")
        table.add_column("Base URL", style="dim")

        for name, cfg in self._app_config.providers.items():
            key = cfg.resolve_api_key(name)
            key_display = f"{key[:8]}..." if key else "[red]not set[/red]"
            table.add_row(
                name,
                "yes" if cfg.enabled else "[dim]no[/dim]",
                key_display,
                cfg.base_url or "[dim]default[/dim]",
            )

        console.print(table)

    async def _fetch_all_usage(self, providers):
        tasks = []
        for name, prov in providers.items():
            tasks.append(self._safe_usage(name, prov))
        return await asyncio.gather(*tasks)

    async def _fetch_all_status(self, providers):
        tasks = []
        for name, prov in providers.items():
            tasks.append(self._safe_status(name, prov))
        return await asyncio.gather(*tasks)

    async def _safe_usage(self, name, prov):
        from harness_usage_status.models import UsageInfo
        try:
            result = await prov.get_usage()
            result.compute_pct()
            return result
        except Exception as e:
            msg = str(e) or type(e).__name__
            return UsageInfo(provider=prov.name, raw={"error": msg})

    async def _safe_status(self, name, prov):
        from harness_usage_status.models import StatusInfo, ProviderState
        try:
            return await prov.get_status()
        except Exception as e:
            return StatusInfo(
                provider=prov.name,
                state=ProviderState.OFFLINE,
                message=str(e),
            )

    def _render_quota_table(self, results):
        table = Table(title="Usage Quota")
        table.add_column("Provider", style="cyan")
        table.add_column("Plan", style="dim")
        table.add_column("Used", justify="right")
        table.add_column("Remaining", justify="right", style="green")
        table.add_column("Limit", justify="right")
        table.add_column("Usage %", justify="right")
        table.add_column("Unit", style="dim")
        table.add_column("Details", style="dim", no_wrap=True)

        for r in results:
            error = r.raw.get("error") if r.raw else None
            if error:
                table.add_row(r.provider, "", "", "", "", f"[red]{error[:50]}[/red]", "", "")
            else:
                pct = f"{r.usage_pct}%" if r.usage_pct is not None else "-"
                pct_style = "red" if r.usage_pct and r.usage_pct > 80 else ""

                # Build details string from raw data
                details = ""
                if r.raw:
                    parts = []
                    # Claude Code format
                    five_hour = r.raw.get("five_hour")
                    seven_day = r.raw.get("seven_day")
                    if five_hour:
                        parts.append(f"5h: {five_hour.get('utilization', '?')}%")
                    if seven_day:
                        parts.append(f"7d: {seven_day.get('utilization', '?')}%")
                    # Codex format
                    pw = r.raw.get("primary_window")
                    sw = r.raw.get("secondary_window")
                    if pw:
                        parts.append(f"5h: {pw.get('used_percent', '?')}%")
                    if sw:
                        parts.append(f"7d: {sw.get('used_percent', '?')}%")
                    # Gemini per-model format
                    gemini_models = r.raw.get("models")
                    if gemini_models:
                        from harness_usage_status.providers.gemini import _format_reset
                        for mid, info in gemini_models.items():
                            short = mid.replace("gemini-", "")
                            reset = _format_reset(info.get("reset_time"))
                            reset_str = f" ↻{reset}" if reset else ""
                            parts.append(f"{short}:{info['remaining_pct']}%{reset_str}")
                    details = "\n".join(parts) if gemini_models else " | ".join(parts)

                table.add_row(
                    r.provider,
                    r.plan or "-",
                    str(r.used) if r.used is not None else "-",
                    str(r.remaining) if r.remaining is not None else "-",
                    str(r.quota_limit) if r.quota_limit is not None else "-",
                    f"[{pct_style}]{pct}[/{pct_style}]" if pct_style else pct,
                    r.unit,
                    details,
                )

        console.print(table)

    def _render_status_table(self, results):
        table = Table(title="Provider Status")
        table.add_column("Provider", style="cyan")
        table.add_column("State")
        table.add_column("Latency", justify="right")
        table.add_column("Last Checked", style="dim")
        table.add_column("Message", style="dim")

        state_styles = {
            "online": "green",
            "offline": "red",
            "degraded": "yellow",
            "unknown": "dim",
        }

        for r in results:
            style = state_styles.get(r.state.value, "")
            table.add_row(
                r.provider,
                f"[{style}]{r.state.value}[/{style}]",
                f"{r.latency_ms}ms" if r.latency_ms else "-",
                r.last_checked.strftime("%H:%M:%S") if r.last_checked else "-",
                r.message or "",
            )

        console.print(table)


def main():
    fire.Fire(HarnessUsageStatus)


if __name__ == "__main__":
    main()
