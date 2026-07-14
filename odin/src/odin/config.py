"""Configuration loading for Odin."""

import logging
import os
import shutil
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

from odin.forced_provider import resolve_forced_provider
from odin.models import (
    AdvisorConfig,
    AgentConfig,
    ChromeDevToolsConfig,
    CostTier,
    MergeAgentConfig,
    ModelRoute,
    OdinConfig,
    TaskItConfig,
)

logger = logging.getLogger("odin.config")

# Config search order:
#   1. Explicit --config path
#   2. ./.odin/config.yaml (project-local)
#   3. ~/.odin/config.yaml (global)
LOCAL_CONFIG_PATH = Path.cwd() / ".odin" / "config.yaml"
GLOBAL_CONFIG_PATH = Path.home() / ".odin" / "config.yaml"

# Env vars for API keys
ENV_VAR_MAP = {
    "minimax": ("MINIMAX_API_KEY",),
    "glm": ("ZAI_API_KEY",),
}


def _expand_env_value(value):
    """Expand ${VAR} config values, returning None for unset pure placeholders."""
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped.startswith("${") and stripped.endswith("}") and stripped.count("${") == 1:
        return os.environ.get(stripped[2:-1])
    return os.path.expanduser(os.path.expandvars(value))


def _discover_local_path(env_name: str, *candidates: str) -> str | None:
    """Resolve a local tool path from env, PATH, or known repo/home locations."""
    env_value = os.environ.get(env_name)
    if env_value:
        return os.path.expanduser(env_value)

    which_value = shutil.which(env_name.lower().replace("_bin", ""))
    if which_value:
        return which_value

    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.exists():
            return str(path)
    return None

# Legacy model routing priority (used as fallback when API routing is unavailable).
# Walk top-to-bottom; first match that is enabled + available wins.
DEFAULT_MODEL_ROUTING = [
    ("glm", "zai-coding-plan/glm-5.2"),
    ("minimax", "minimax-coding-plan/MiniMax-M3"),
    ("agy", "Gemini 3.5 Flash (High)"),
    ("claude", "claude-sonnet-5"),
    ("codex", "gpt-5.5"),
    ("claude", "claude-opus-4-8"),
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


def _first_env_value(env_names: tuple[str, ...]) -> str | None:
    for env_name in env_names:
        value = os.environ.get(env_name)
        if value:
            return value
    return None


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
    for agent_name, env_vars in ENV_VAR_MAP.items():
        if agent_name not in agents:
            continue
        cfg = agents[agent_name]
        key = _first_env_value(env_vars)
        if key and not cfg.api_key:
            cfg.api_key = key
        if agent_name in explicitly_disabled:
            continue
        if not cfg.enabled and key:
            cfg.enabled = True


def _filter_unknown_harnesses(agents: dict) -> dict:
    """Remove agents whose harness name is not registered.

    A stale config entry (e.g. a harness removed from the registry) should not
    take down planning or execution.  Each unknown agent is dropped with a
    warning so the operator knows to clean up their config, while the
    remaining valid agents are returned unchanged.
    """
    from odin.harnesses.registry import HARNESS_REGISTRY

    known = set(HARNESS_REGISTRY.keys())
    filtered = {}
    for name, cfg in agents.items():
        if name in known:
            filtered[name] = cfg
        else:
            logger.warning(
                "Skipping unknown harness '%s' — not in registry "
                "(available: %s). Remove it from your config to silence this warning.",
                name,
                sorted(known),
            )
    return filtered


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
        api_key = _expand_env_value(cfg.get("api_key"))

        cost_tier = cfg.get("cost_tier", "medium")
        known_keys = {
            "enabled", "cli_command", "api_key", "base_url",
            "capabilities", "cost_tier", "execute_args", "models",
            "default_model", "premium_model", "run_in_forkd",
            "forkd_bin", "forkd_kernel", "forkd_scripts_dir",
            "forkd_use_sudo", "forkd_mode", "forkd_controller_url",
            "forkd_snapshot_tag", "forkd_per_child_netns",
            "forkd_image", "forkd_extra",
            "forkd_cache_dir", "forkd_rootfs_size_mib", "forkd_mem_size_mib", "forkd_tap",
            "forkd_init_git", "forkd_workspace_excludes",
            "sandbox_mode", "microsandbox_bin", "microsandbox_image",
            "microsandbox_snapshot", "microsandbox_workspace_mount",
            "microsandbox_mem_size_mib", "microsandbox_cpus",
            "microsandbox_timeout_secs", "microsandbox_host_ip",
            "microsandbox_claude_token_file",
            "microsandbox_net_default", "microsandbox_net_rules",
            "microsandbox_extra_args",
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
            run_in_forkd=cfg.get("run_in_forkd", False),
            forkd_bin=_expand_env_value(cfg.get("forkd_bin")),
            forkd_kernel=_expand_env_value(cfg.get("forkd_kernel")),
            forkd_scripts_dir=_expand_env_value(cfg.get("forkd_scripts_dir")),
            forkd_use_sudo=cfg.get("forkd_use_sudo", False),
            forkd_mode=cfg.get("forkd_mode", "cli"),
            forkd_controller_url=cfg.get("forkd_controller_url", "http://127.0.0.1:8889"),
            forkd_snapshot_tag=cfg.get("forkd_snapshot_tag"),
            forkd_per_child_netns=cfg.get("forkd_per_child_netns", True),
            forkd_image=cfg.get("forkd_image", "node:22-slim"),
            forkd_extra=cfg.get("forkd_extra", ["python3", "ca-certificates", "git", "chromium"]),
            forkd_cache_dir=_expand_env_value(cfg.get("forkd_cache_dir", "~/.cache/odin/forkd")),
            forkd_rootfs_size_mib=cfg.get("forkd_rootfs_size_mib", 4096),
            forkd_mem_size_mib=cfg.get("forkd_mem_size_mib", 4096),
            forkd_tap=cfg.get("forkd_tap", "forkd-tap0"),
            forkd_init_git=cfg.get("forkd_init_git", True),
            forkd_workspace_excludes=cfg.get(
                "forkd_workspace_excludes",
                AgentConfig().forkd_workspace_excludes,
            ),
            sandbox_mode=cfg.get("sandbox_mode", "none"),
            microsandbox_bin=_expand_env_value(cfg.get("microsandbox_bin")),
            microsandbox_image=cfg.get("microsandbox_image", "node:22-slim"),
            microsandbox_snapshot=cfg.get("microsandbox_snapshot"),
            microsandbox_workspace_mount=cfg.get("microsandbox_workspace_mount", "/workspace"),
            microsandbox_mem_size_mib=cfg.get("microsandbox_mem_size_mib", 4096),
            microsandbox_cpus=cfg.get("microsandbox_cpus"),
            microsandbox_timeout_secs=cfg.get("microsandbox_timeout_secs", 1800),
            microsandbox_host_ip=cfg.get("microsandbox_host_ip"),
            microsandbox_claude_token_file=_expand_env_value(cfg.get("microsandbox_claude_token_file")),
            microsandbox_net_default=cfg.get("microsandbox_net_default"),
            microsandbox_net_rules=cfg.get("microsandbox_net_rules", []),
            microsandbox_extra_args=cfg.get("microsandbox_extra_args", []),
            extras=extras,
        )

        if agents[name].run_in_forkd:
            agents[name].forkd_bin = agents[name].forkd_bin or _discover_local_path(
                "FORKD_BIN",
                "~/forkd-poc/bin/forkd",
                "~/bin/forkd",
            )
            agents[name].forkd_kernel = agents[name].forkd_kernel or _discover_local_path(
                "FORKD_KERNEL",
                "~/forkd-poc/vmlinux",
                "/var/lib/forkd/kernels/vmlinux",
                "/var/lib/forkd/vmlinux",
            )
            agents[name].forkd_scripts_dir = agents[name].forkd_scripts_dir or _discover_local_path(
                "FORKD_SCRIPTS_DIR",
                "~/forkd-poc/forkd/scripts",
                "/usr/local/share/forkd/scripts",
                "/opt/forkd/scripts",
            )

        if agents[name].sandbox_mode == "microsandbox":
            agents[name].microsandbox_bin = agents[name].microsandbox_bin or _discover_local_path(
                "MICROSANDBOX_BIN",
                "~/.local/bin/msb",
                "~/.microsandbox/bin/msb",
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

    # Fail-soft: drop agents whose harness is not registered so a stale
    # config entry (e.g. a removed harness) doesn't abort planning.
    agents = _filter_unknown_harnesses(agents)

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

    # Parse merge_agent config section
    raw_merge_agent = raw.get("merge_agent")
    if raw_merge_agent and isinstance(raw_merge_agent, dict):
        merge_agent_cfg = MergeAgentConfig(**raw_merge_agent)
    else:
        merge_agent_cfg = MergeAgentConfig()

    # Parse advisor config section
    raw_advisor = raw.get("advisor")
    if raw_advisor and isinstance(raw_advisor, dict):
        advisor_cfg = AdvisorConfig(**raw_advisor)
    else:
        advisor_cfg = AdvisorConfig()

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
        max_turns=raw.get("max_turns"),
        worktree_enabled=worktree_enabled,
        base_branch=base_branch,
        worktree_dir=worktree_dir,
        worktree_post_hooks=worktree_post_hooks,
        worktree_symlinks=worktree_symlinks,
        auto_finalize=auto_finalize,
        merge_agent=merge_agent_cfg,
        advisor=advisor_cfg,
        project_notes_path=raw.get("project_notes_path"),
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
            capabilities=["reasoning", "planning", "coding", "writing", "run_shell_command", "read_file", "write_file"],
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
            capabilities=["coding", "writing", "run_shell_command", "read_file", "write_file"],
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
            capabilities=["coding", "writing", "research", "run_shell_command", "read_file", "write_file"],
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
            cli_command="opencode",
            capabilities=["coding", "writing", "run_shell_command", "read_file", "write_file"],
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
            capabilities=["coding", "writing", "run_shell_command", "read_file", "write_file"],
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

    # Fail-soft: drop agents whose harness is not registered.
    agents = _filter_unknown_harnesses(agents)

    cfg = OdinConfig(
        agents=agents,
        model_routing=_default_model_routing(),
        config_source=source,
        board_backend="taskit",
        taskit=_apply_taskit_auth_env(TaskItConfig()),
        execution_timeout_seconds=1800,
    )
    return _apply_forced_provider_env(cfg)
