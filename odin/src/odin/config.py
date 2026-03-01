"""Configuration loading for Odin."""

import os
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

from odin.models import (
    AgentConfig, ChromeDevToolsConfig, CostTier, ModelRoute, OdinConfig, TaskItConfig,
)

# Config search order:
#   1. Explicit --config path
#   2. ./.odin/config.yaml (project-local)
#   3. ~/.odin/config.yaml (global)
LOCAL_CONFIG_PATH = Path.cwd() / ".odin" / "config.yaml"
GLOBAL_CONFIG_PATH = Path.home() / ".odin" / "config.yaml"

# Env vars for API keys
ENV_VAR_MAP = {
    "minimax": "MINIMAX_API_KEY",
    "glm": "ZAI_API_KEY",
}

# Legacy model routing priority (used as fallback when API routing is unavailable).
# Walk top-to-bottom; first match that is enabled + available wins.
DEFAULT_MODEL_ROUTING = [
    ("qwen", "qwen3-coder"),
    ("gemini", "gemini-3-flash-preview"),
    ("glm", "zai-coding-plan/glm-4.7"),
    ("minimax", "minimax-coding-plan/MiniMax-M2.5"),
    ("glm", "zai-coding-plan/glm-5"),
    ("gemini", "gemini-3-pro-preview"),
    ("claude", "claude-sonnet-4-5"),
    ("codex", "gpt-5.3-codex"),
    ("claude", "claude-opus-4-6"),
]


def load_config(config_path: Optional[str] = None) -> OdinConfig:
    """Load Odin config, merging global and local sources."""
    # Load .env files
    env_path = Path.cwd() / ".env"
    if env_path.exists():
        load_dotenv(env_path)

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


def _parse_models(raw_models) -> dict:
    """Parse models from YAML — supports both list and dict formats.

    List format (simple):
        models: [model-a, model-b]
    Dict format (with notes):
        models:
          model-a: "fast, cheap"
          model-b: "highest quality"
    """
    if isinstance(raw_models, dict):
        return {k: (v or "") for k, v in raw_models.items()}
    if isinstance(raw_models, list):
        return {m: "" for m in raw_models}
    return {}


def _apply_yolo_mode(
    agents: dict,
    explicitly_disabled: Optional[set] = None,
) -> None:
    """Auto-enable API-based agents when their API key is present in env.

    If an agent is disabled but its env var key exists, flip to enabled —
    UNLESS the agent was explicitly disabled in the config file (the user's
    intent takes priority over env-var auto-discovery).
    """
    explicitly_disabled = explicitly_disabled or set()
    for agent_name, env_var in ENV_VAR_MAP.items():
        if agent_name not in agents:
            continue
        if agent_name in explicitly_disabled:
            continue
        cfg = agents[agent_name]
        if not cfg.enabled and os.environ.get(env_var):
            cfg.enabled = True
            if not cfg.api_key:
                cfg.api_key = os.environ.get(env_var)


def _parse_model_routing(raw_list) -> list:
    """Parse model_routing from YAML into ModelRoute objects."""
    if not raw_list or not isinstance(raw_list, list):
        return []
    routes = []
    for entry in raw_list:
        if isinstance(entry, dict) and "agent" in entry and "model" in entry:
            routes.append(ModelRoute(agent=entry["agent"], model=entry["model"]))
    return routes


def _default_model_routing() -> list:
    """Return the built-in default model routing priority list."""
    return [ModelRoute(agent=a, model=m) for a, m in DEFAULT_MODEL_ROUTING]


def _apply_taskit_auth_env(cfg: TaskItConfig) -> TaskItConfig:
    """Overlay auth env vars onto a TaskItConfig.

    Env vars (from .env or shell):
      ODIN_ADMIN_USER     -> admin_email
      ODIN_ADMIN_PASSWORD -> admin_password
    """
    email = os.environ.get("ODIN_ADMIN_USER") or cfg.admin_email
    password = os.environ.get("ODIN_ADMIN_PASSWORD") or cfg.admin_password
    if email or password:
        cfg = cfg.model_copy(update={
            "admin_email": email,
            "admin_password": password,
        })
    return cfg


def _load_from_yaml(path: Path, source: str) -> OdinConfig:
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    agents = {}
    explicitly_disabled: set = set()
    for name, cfg in raw.get("agents", {}).items():
        if cfg is None:
            cfg = {}

        # Track agents the user explicitly set to enabled: false
        if "enabled" in cfg and not cfg["enabled"]:
            explicitly_disabled.add(name)

        # Resolve API key from env if placeholder
        api_key = cfg.get("api_key")
        if api_key and api_key.startswith("${") and api_key.endswith("}"):
            env_var = api_key[2:-1]
            api_key = os.environ.get(env_var)

        cost_tier = cfg.get("cost_tier", "medium")
        known_keys = {
            "enabled", "cli_command", "api_key", "base_url",
            "capabilities", "cost_tier", "execute_args", "models",
            "default_model", "premium_model",
        }
        extras = {k: v for k, v in cfg.items() if k not in known_keys}

        agents[name] = AgentConfig(
            enabled=cfg.get("enabled", True),
            cli_command=cfg.get("cli_command"),
            api_key=api_key,
            base_url=cfg.get("base_url"),
            capabilities=cfg.get("capabilities", []),
            cost_tier=CostTier(cost_tier),
            models=_parse_models(cfg.get("models", {})),
            default_model=cfg.get("default_model"),
            premium_model=cfg.get("premium_model"),
            execute_args=cfg.get("execute_args"),
            extras=extras,
        )

    # Merge built-in defaults for fields the YAML didn't set.
    # The YAML config is a sparse overlay (cli_command, api_key, etc.);
    # metadata like models, default_model, premium_model comes from defaults.
    builtin_defaults = _default_config("builtin").agents
    for name, yaml_cfg in agents.items():
        default_cfg = builtin_defaults.get(name)
        if default_cfg is None:
            continue
        if not yaml_cfg.models:
            yaml_cfg.models = default_cfg.models
        if yaml_cfg.default_model is None:
            yaml_cfg.default_model = default_cfg.default_model
        if yaml_cfg.premium_model is None:
            yaml_cfg.premium_model = default_cfg.premium_model
        if not yaml_cfg.capabilities:
            yaml_cfg.capabilities = default_cfg.capabilities
        if yaml_cfg.cost_tier == CostTier.MEDIUM and default_cfg.cost_tier != CostTier.MEDIUM:
            yaml_cfg.cost_tier = default_cfg.cost_tier

    # Yolo mode: auto-enable API agents when keys are present
    # (but respect explicit disables from the config file)
    _apply_yolo_mode(agents, explicitly_disabled)

    # Parse model routing (fall back to defaults if not specified)
    raw_routing = raw.get("model_routing")
    model_routing = _parse_model_routing(raw_routing) if raw_routing else _default_model_routing()

    # Parse taskit config section
    taskit_cfg = None
    raw_taskit = raw.get("taskit")
    if raw_taskit and isinstance(raw_taskit, dict):
        taskit_cfg = TaskItConfig(**raw_taskit)

    # Overlay Firebase auth from env vars onto taskit config
    taskit_cfg = _apply_taskit_auth_env(taskit_cfg or TaskItConfig())

    # Parse chrome_devtools config section
    chrome_devtools_cfg = None
    raw_cd = raw.get("chrome_devtools")
    if raw_cd and isinstance(raw_cd, dict):
        chrome_devtools_cfg = ChromeDevToolsConfig(**raw_cd)

    return OdinConfig(
        base_agent=raw.get("base_agent", "claude"),
        agents=agents,
        model_routing=model_routing,
        banned_models=raw.get("banned_models", []),
        task_storage=raw.get("task_storage", ".odin/tasks"),
        log_dir=raw.get("log_dir", ".odin/logs"),
        cost_storage=raw.get("cost_storage", ".odin/costs"),
        config_source=source,
        board_backend=raw.get("board_backend", "taskit"),
        taskit=taskit_cfg if taskit_cfg else TaskItConfig(),
        chrome_devtools=chrome_devtools_cfg,
        mcps=raw.get("mcps", ["taskit", "mobile", "chrome-devtools"]),
        execution_timeout_seconds=raw.get("execution_timeout_seconds", 1800),
    )


def _default_config(source: str) -> OdinConfig:
    """Generate default config with common agents.

    Agent metadata here is a fallback for when the TaskIt API routing-config
    endpoint is unavailable. The canonical source of truth for agent metadata
    (capabilities, cost_tier, models, etc.) is agent_models.json -> seedmodels -> DB.
    """
    agents = {
        "claude": AgentConfig(
            cli_command="claude",
            capabilities=["reasoning", "planning", "coding", "writing"],
            cost_tier=CostTier.HIGH,
            models={
                "claude-opus-4-6": "latest opus, highest quality",
                "claude-sonnet-4-5": "fast + capable",
                "claude-opus-4": "previous opus, 3x cost",
                "claude-haiku-4-5": "fast, cheapest, good for simple tasks",
            },
            default_model="claude-sonnet-4-5",
            premium_model="claude-opus-4-6",
        ),
        "codex": AgentConfig(
            cli_command="codex",
            capabilities=["coding", "writing"],
            cost_tier=CostTier.MEDIUM,
            models={
                "gpt-5.3-codex": "default, optimized for code",
                "o3": "strong reasoning",
                "o4-mini": "fast, cheaper",
            },
            default_model="gpt-5.3-codex",
            premium_model="gpt-5.3-codex",
        ),
        "gemini": AgentConfig(
            cli_command="gemini",
            capabilities=["coding", "writing", "research"],
            cost_tier=CostTier.LOW,
            models={
                "gemini-2.5-pro": "strongest, highest quota usage",
                "gemini-2.5-flash": "balanced speed/quality",
                "gemini-2.0-flash": "fastest, lowest quota usage",
                "gemini-3-pro-preview": "next-gen preview, strong reasoning",
                "gemini-3-flash-preview": "next-gen preview, fast",
            },
            default_model="gemini-3-flash-preview",
            premium_model="gemini-3-pro-preview",
        ),
        "qwen": AgentConfig(
            cli_command="qwen",
            capabilities=["coding", "writing"],
            cost_tier=CostTier.LOW,
            models={
                "qwen3-coder": "default, code-optimized",
            },
            default_model="qwen3-coder",
            premium_model="qwen3-coder",
        ),
        "minimax": AgentConfig(
            cli_command="kilo",
            capabilities=["coding", "writing"],
            cost_tier=CostTier.LOW,
            models={
                "minimax-coding-plan/MiniMax-M2": "balanced, general purpose",
                "minimax-coding-plan/MiniMax-M2.1": "improved reasoning",
                "minimax-coding-plan/MiniMax-M2.5": "latest, strongest",
            },
            default_model="minimax-coding-plan/MiniMax-M2.5",
            premium_model="minimax-coding-plan/MiniMax-M2.5",
        ),
        "glm": AgentConfig(
            cli_command="opencode",
            capabilities=["coding", "writing"],
            cost_tier=CostTier.LOW,
            models={
                "zai-coding-plan/glm-5": "opus-level, 3x token usage over glm-4.7",
                "zai-coding-plan/glm-4.7": "default, balanced cost/quality",
                "zai-coding-plan/glm-4.7-flash": "fast, low cost",
                "zai-coding-plan/glm-4.6": "previous gen, stable",
                "zai-coding-plan/glm-4.6v": "vision-capable",
                "zai-coding-plan/glm-4.5-air": "lightweight, cheapest",
                "zai-coding-plan/glm-4.5-flash": "fast, budget option",
                "zai-coding-plan/glm-4.5v": "vision, previous gen",
            },
            default_model="zai-coding-plan/glm-4.7",
            premium_model="zai-coding-plan/glm-5",
        ),
    }
    # Yolo mode: auto-enable API agents when keys are present
    _apply_yolo_mode(agents)

    return OdinConfig(
        agents=agents,
        model_routing=_default_model_routing(),
        config_source=source,
        board_backend="taskit",
        taskit=_apply_taskit_auth_env(TaskItConfig()),
        execution_timeout_seconds=1800,
    )
