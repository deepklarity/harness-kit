"""Configuration loading for Odin."""

import os
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

from odin.forced_provider import resolve_forced_provider
from odin.models import (
    AgentConfig,
    ChromeDevToolsConfig,
    CostTier,
    ModelRoute,
    OdinConfig,
    TaskItConfig,
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
    ("gemini", "gemini-3-flash-preview"),
    ("glm", "zai-coding-plan/glm-4.7"),
    ("minimax", "minimax-coding-plan/MiniMax-M2.7"),
    ("glm", "zai-coding-plan/glm-5.1"),
    ("gemini", "gemini-3-pro-preview"),
    ("claude", "claude-sonnet-4-6"),
    ("codex", "gpt-5.4"),
    ("claude", "claude-opus-4-7"),
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


def _apply_forced_provider_env(cfg: OdinConfig) -> OdinConfig:
    """Overlay forced provider env onto config and validate it."""
    forced = resolve_forced_provider(cfg)
    if not forced.enabled:
        return cfg
    return cfg.model_copy(update={
        "forced_base_provider": forced.provider,
        "forced_base_model": forced.model,
    })


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
    # Agents not mentioned in YAML at all get the full built-in default.
    builtin_defaults = _default_config("builtin").agents
    for name, default_cfg in builtin_defaults.items():
        if name not in agents:
            agents[name] = default_cfg
            continue
        yaml_cfg = agents[name]
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

    # Parse worktree config section (or top-level keys)
    raw_worktree = raw.get("worktree", {}) or {}
    worktree_enabled = raw_worktree.get("enabled", raw.get("worktree_enabled", True))
    base_branch = raw_worktree.get("base_branch", raw.get("base_branch", "main"))
    worktree_dir = raw_worktree.get("dir", raw.get("worktree_dir", ".odin/worktrees"))
    worktree_post_hooks = raw_worktree.get("post_hooks", raw.get("worktree_post_hooks", []))
    worktree_symlinks = raw_worktree.get("symlinks", raw.get("worktree_symlinks", []))
    auto_finalize = raw_worktree.get("auto_finalize", raw.get("auto_finalize", True))

    cfg = OdinConfig(
        base_agent=raw.get("base_agent", "claude"),
        base_model=raw.get("base_model"),
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
        worktree_enabled=worktree_enabled,
        base_branch=base_branch,
        worktree_dir=worktree_dir,
        worktree_post_hooks=worktree_post_hooks,
        worktree_symlinks=worktree_symlinks,
        auto_finalize=auto_finalize,
    )
    return _apply_forced_provider_env(cfg)


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
                "claude-sonnet-4-6": "default — Sonnet 4.6, fast and capable (1M context)",
                "claude-opus-4-7": "premium — flagship Opus 4.7, strongest agentic coding (1M context)",
                "claude-sonnet-4-5": "previous Sonnet, superseded by 4.6",
                "claude-opus-4-6": "previous Opus, superseded by 4.7",
                "claude-haiku-4-5": "cheapest — fastest, good for simple tasks",
            },
            default_model="claude-sonnet-4-6",
            premium_model="claude-opus-4-7",
        ),
        "codex": AgentConfig(
            cli_command="codex",
            capabilities=["coding", "writing"],
            cost_tier=CostTier.MEDIUM,
            models={
                "gpt-5.4": "default — strong everyday coding",
                "gpt-5.5": "premium — frontier flagship (1M context)",
                "gpt-5.4-mini": "cheapest — faster, lower cost for light tasks",
                "gpt-5.3-codex": "previous codex-tuned model",
                "gpt-5.2": "previous-gen general model (400K context)",
            },
            default_model="gpt-5.4",
            premium_model="gpt-5.5",
        ),
        "gemini": AgentConfig(
            cli_command="gemini",
            capabilities=["coding", "writing", "research"],
            cost_tier=CostTier.LOW,
            models={
                "gemini-3-flash-preview": "default — Gemini 3 Flash, balanced speed/quality",
                "gemini-3.1-pro-preview": "premium — Gemini 3.1 Pro, strongest reasoning (1M context)",
                "gemini-2.5-flash-lite": "cheapest — lowest cost for high-volume tasks",
                "gemini-3.1-flash-lite-preview": "newer cost-effective tier",
                "gemini-2.5-pro": "previous Pro, cheaper alternative",
                "gemini-2.5-flash": "previous Flash, cheaper alternative",
            },
            default_model="gemini-3-flash-preview",
            premium_model="gemini-3.1-pro-preview",
        ),
        "minimax": AgentConfig(
            cli_command="kilo",
            capabilities=["coding", "writing"],
            cost_tier=CostTier.LOW,
            models={
                "minimax-coding-plan/MiniMax-M2.7": "default — latest, strongest agentic coding",
                "minimax-coding-plan/MiniMax-M2.5": "previous flagship",
                "minimax-coding-plan/MiniMax-M2.1": "previous, improved multi-language programming",
                "minimax-coding-plan/MiniMax-M2": "original M2, balanced general-purpose",
            },
            default_model="minimax-coding-plan/MiniMax-M2.7",
            premium_model="minimax-coding-plan/MiniMax-M2.7",
        ),
        "glm": AgentConfig(
            cli_command="opencode",
            capabilities=["coding", "writing"],
            cost_tier=CostTier.LOW,
            models={
                "zai-coding-plan/glm-4.7": "default — GLM-4.7, balanced cost/quality",
                "zai-coding-plan/glm-5.1": "premium — GLM-5.1 latest flagship",
                "zai-coding-plan/glm-5-turbo": "faster GLM-5 variant, lower cost than 5.1",
                "zai-coding-plan/glm-4.5-air": "cheapest — lightweight, high-volume use",
            },
            default_model="zai-coding-plan/glm-4.7",
            premium_model="zai-coding-plan/glm-5.1",
        ),
    }
    # Yolo mode: auto-enable API agents when keys are present
    _apply_yolo_mode(agents)

    cfg = OdinConfig(
        agents=agents,
        model_routing=_default_model_routing(),
        config_source=source,
        board_backend="taskit",
        taskit=_apply_taskit_auth_env(TaskItConfig()),
        execution_timeout_seconds=1800,
    )
    return _apply_forced_provider_env(cfg)
