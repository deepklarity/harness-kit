"""Forced Gemini provider configuration and validation."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

ALLOWED_FORCED_PROVIDERS = {"gemini"}


@dataclass(frozen=True)
class ForcedProviderSelection:
    enabled: bool
    provider: Optional[str] = None
    model: Optional[str] = None
    source: str = "legacy"


@lru_cache(maxsize=1)
def _load_agent_catalog() -> Dict[str, Any]:
    catalog_path = (
        Path(__file__).resolve().parent.parent / "data" / "agent_models.json"
    )
    with catalog_path.open() as f:
        return json.load(f)


def _normalize_provider(raw: Optional[str]) -> Optional[str]:
    value = (raw or "").strip().lower()
    return value or None


def _normalize_model(raw: Optional[str]) -> Optional[str]:
    value = (raw or "").strip()
    return value or None


def _provider_entry(provider: str) -> Dict[str, Any]:
    catalog = _load_agent_catalog()
    agents = catalog.get("agents", {})
    entry = agents.get(provider)
    if not entry:
        raise ImproperlyConfigured(
            f"FORCED_BASE_PROVIDER '{provider}' is not defined in agent_models.json."
        )
    return entry


def get_forced_provider_selection() -> ForcedProviderSelection:
    """Return the effective forced provider selection from env-backed settings."""
    provider = _normalize_provider(getattr(settings, "FORCED_BASE_PROVIDER", None))
    model = _normalize_model(getattr(settings, "FORCED_BASE_MODEL", None))

    if not provider:
        return ForcedProviderSelection(enabled=False)

    if provider not in ALLOWED_FORCED_PROVIDERS:
        allowed = ", ".join(sorted(ALLOWED_FORCED_PROVIDERS))
        raise ImproperlyConfigured(
            f"FORCED_BASE_PROVIDER must be one of: {allowed}."
        )

    entry = _provider_entry(provider)
    cli_command = entry.get("cli_command") or provider
    if shutil.which(cli_command) is None:
        raise ImproperlyConfigured(
            f"Forced provider '{provider}' is unavailable: CLI '{cli_command}' not found on PATH."
        )

    model_names = [m.get("name") for m in entry.get("models", []) if m.get("name")]
    if model:
        if model not in model_names:
            raise ImproperlyConfigured(
                f"FORCED_BASE_MODEL '{model}' does not belong to provider '{provider}'."
            )
        resolved_model = model
    else:
        resolved_model = entry.get("default_model") or (model_names[0] if model_names else None)

    if not resolved_model:
        raise ImproperlyConfigured(
            f"Forced provider '{provider}' does not have a usable default model."
        )

    return ForcedProviderSelection(
        enabled=True,
        provider=provider,
        model=resolved_model,
        source="env",
    )


def validate_forced_provider_config() -> None:
    """Validate forced provider env at startup."""
    get_forced_provider_selection()


def is_model_allowed_for_forced_provider(model_name: Optional[str]) -> bool:
    """Return whether a model is permitted under the active forced-provider mode."""
    selection = get_forced_provider_selection()
    if not selection.enabled or not model_name:
        return True
    if selection.model:
        forced_model_raw = (getattr(settings, "FORCED_BASE_MODEL", None) or "").strip()
        if forced_model_raw:
            return model_name == selection.model
    return model_name in _provider_model_names(selection.provider or "")


def _provider_model_names(provider: str) -> set[str]:
    entry = _provider_entry(provider)
    return {
        m.get("name")
        for m in entry.get("models", [])
        if m.get("name")
    }
