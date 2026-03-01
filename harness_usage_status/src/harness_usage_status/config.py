import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


# Config search order:
#   1. Explicit --config path
#   2. ./config/config.yaml  (CWD local override)
#   3. ~/.config/harness_usage_status/config.yaml  (global default)
LOCAL_CONFIG_DIR = Path.cwd() / "config"
LOCAL_CONFIG_PATH = LOCAL_CONFIG_DIR / "config.yaml"
LOCAL_ENV_PATH = LOCAL_CONFIG_DIR / ".env"
PROJECT_ROOT_ENV_PATH = Path.cwd() / ".env"
GLOBAL_CONFIG_PATH = Path.home() / ".config" / "harness_usage_status" / "config.yaml"

# Env var names for API keys (only for API-based providers)
ENV_VAR_MAP = {
    "minimax": "MINIMAX_API_KEY",
    "glm": "ZAI_API_KEY",
}

# All known provider names (CLI-based + API-based)
ALL_PROVIDERS = ["claude_code", "codex", "gemini", "qwen", "minimax", "glm"]


class ProviderConfig(BaseModel):
    """Configuration for a single provider."""
    enabled: bool = True
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    # Extra provider-specific settings stored here
    extras: Dict[str, Any] = Field(default_factory=dict)

    def resolve_api_key(self, provider_name: str) -> Optional[str]:
        """Return api_key from config, falling back to env var."""
        if self.api_key:
            return self.api_key
        env_var = ENV_VAR_MAP.get(provider_name)
        if env_var:
            return os.environ.get(env_var)
        return None


class AppConfig(BaseModel):
    """Top-level application configuration."""
    providers: Dict[str, ProviderConfig] = Field(default_factory=dict)
    config_source: Optional[str] = None

    def get_provider_configs(self) -> Dict[str, dict]:
        """Return provider configs as dicts with resolved API keys."""
        result = {}
        for name, cfg in self.providers.items():
            d = cfg.model_dump()
            d["api_key"] = cfg.resolve_api_key(name)
            result[name] = d
        return result

    def enabled_providers(self) -> Dict[str, ProviderConfig]:
        """Return only enabled providers."""
        return {k: v for k, v in self.providers.items() if v.enabled}


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """Load config from YAML file, falling back to defaults.

    Search order:
      1. Explicit --config path
      2. ./config/config.yaml  (CWD local override)
      3. ~/.config/harness_usage_status/config.yaml  (global)

    Also loads ./config/.env if it exists (for API keys).
    """
    # Load .env — project root first, then config dir (later values override)
    if PROJECT_ROOT_ENV_PATH.exists():
        load_dotenv(PROJECT_ROOT_ENV_PATH)
    if LOCAL_ENV_PATH.exists():
        load_dotenv(LOCAL_ENV_PATH, override=True)

    # Resolve config file path
    if config_path:
        path = Path(config_path)
        source = str(path)
    elif LOCAL_CONFIG_PATH.exists():
        path = LOCAL_CONFIG_PATH
        source = f"{path} (local)"
    elif GLOBAL_CONFIG_PATH.exists():
        path = GLOBAL_CONFIG_PATH
        source = f"{path} (global)"
    else:
        return _default_config("defaults (no config file found)")

    return _load_from_yaml(path, source)


def _load_from_yaml(path: Path, source: str) -> AppConfig:
    """Parse a YAML config file into AppConfig."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    providers = {}
    for name, cfg in raw.get("providers", {}).items():
        if cfg is None:
            cfg = {}
        extras = {k: v for k, v in cfg.items() if k not in ("enabled", "api_key", "base_url")}
        providers[name] = ProviderConfig(
            enabled=cfg.get("enabled", True),
            api_key=cfg.get("api_key"),
            base_url=cfg.get("base_url"),
            extras=extras,
        )

    return AppConfig(providers=providers, config_source=source)


def _default_config(source: str) -> AppConfig:
    """Generate a default config with all providers enabled (using env vars)."""
    providers = {name: ProviderConfig() for name in ALL_PROVIDERS}
    return AppConfig(providers=providers, config_source=source)
