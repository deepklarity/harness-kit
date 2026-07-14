"""Pydantic models for Odin orchestration."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CostTier(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AgentConfig(BaseModel):
    """Configuration for a single agent."""

    enabled: bool = True
    cli_command: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    capabilities: List[str] = Field(default_factory=list)
    cost_tier: CostTier = CostTier.MEDIUM
    models: Dict[str, str] = Field(default_factory=dict)
    default_model: Optional[str] = None
    premium_model: Optional[str] = None
    execute_args: Optional[str] = None
    # --- forkd sandbox (linux microVM; legacy, retained for backcompat) ---
    run_in_forkd: bool = False
    forkd_bin: Optional[str] = None
    forkd_kernel: Optional[str] = None
    forkd_scripts_dir: Optional[str] = None
    forkd_use_sudo: bool = False
    forkd_mode: str = "cli"
    forkd_controller_url: str = "http://127.0.0.1:8889"
    forkd_snapshot_tag: Optional[str] = None
    forkd_per_child_netns: bool = True
    forkd_image: str = "node:22-slim"
    forkd_extra: List[str] = Field(default_factory=lambda: ["python3", "ca-certificates", "git", "chromium"])
    forkd_cache_dir: str = "~/.cache/odin/forkd"
    forkd_rootfs_size_mib: int = 4096
    forkd_mem_size_mib: int = 4096
    forkd_tap: str = "forkd-tap0"
    forkd_init_git: bool = True
    forkd_workspace_excludes: List[str] = Field(default_factory=lambda: [
        ".git",
        ".claude",
        ".codex",
        ".gemini",
        ".kilocode",
        ".opencode",
        ".qwen",
        ".odin",
        ".env",
        ".mcp.json",
        "node_modules",
        "opencode.json",
    ])
    # --- microsandbox (libkrun microVM sandbox; runs on macOS HVF + Linux KVM) ---
    sandbox_mode: str = "none"  # none | forkd | microsandbox (docker/seatbelt planned)
    microsandbox_bin: Optional[str] = None
    microsandbox_image: str = "node:22-slim"
    microsandbox_snapshot: Optional[str] = None  # boot from a provisioned snapshot (e.g. "odin-agents")
    microsandbox_workspace_mount: str = "/workspace"
    microsandbox_mem_size_mib: int = 4096  # opencode (bun) OOMs below ~4G
    microsandbox_cpus: Optional[int] = None
    microsandbox_timeout_secs: int = 1800
    microsandbox_host_ip: Optional[str] = None  # LAN IP the guest reaches the host by (auto-detect if None)
    microsandbox_claude_token_file: Optional[str] = None  # path to a .claude-token file (macOS claude OAuth)
    microsandbox_agy_token_file: Optional[str] = None  # path to a file with agy's raw OAuth JSON (headless fallback)
    microsandbox_net_default: Optional[str] = None  # e.g. "deny" for an egress allowlist
    microsandbox_net_rules: List[str] = Field(default_factory=list)  # e.g. ["allow@public"]
    microsandbox_extra_args: List[str] = Field(default_factory=list)
    extras: Dict[str, Any] = Field(default_factory=dict)


class TaskStatus(str, Enum):
    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    TESTING = "testing"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


class TaskSpec(BaseModel):
    """Input task specification."""

    title: str
    description: str
    required_capabilities: List[str] = Field(default_factory=list)
    suggested_agent: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SubTask(BaseModel):
    """A decomposed sub-task with assignment info."""

    id: str
    title: str
    description: str
    required_capabilities: List[str] = Field(default_factory=list)
    assigned_agent: Optional[str] = None
    depends_on: List[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.BACKLOG
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TaskResult(BaseModel):
    """Output from an agent execution."""

    success: bool
    output: str = ""
    error: Optional[str] = None
    duration_ms: Optional[float] = None
    agent: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ModelRoute(BaseModel):
    """A single entry in the model routing priority list."""

    agent: str   # e.g. "gemini", "glm", "claude"
    model: str   # e.g. "coder-model", "GLM-4.7"


class ChromeDevToolsConfig(BaseModel):
    """Configuration for chrome-devtools MCP server."""

    headless: bool = True

    # CDP endpoint of a browser running OUTSIDE the guest. Used by microsandbox
    # agents: the aarch64 libkrun microVM cannot launch Chromium (it SIGTRAPs in
    # early bring-up), so the in-guest MCP connects to a host-side browser over
    # CDP instead of launching one. The staged MCP config rewrites 127.0.0.1 ->
    # host LAN IP, so the default reaches the host's :9222. Set explicitly to
    # point at a different host/port. Ignored when Chrome is launched in-guest
    # (forkd, where Chromium works). None => derive the default for microsandbox.
    browser_url: Optional[str] = None


class TaskItConfig(BaseModel):
    """Configuration for connecting to a TaskIt instance."""

    base_url: str = "http://localhost:9100"
    board_id: int = 1
    created_by: str = "odin@harness.kit"
    trial_board_name: str = "odin-trial"
    # Auth credentials (loaded from env: ODIN_ADMIN_USER, ODIN_ADMIN_PASSWORD)
    admin_email: Optional[str] = None
    admin_password: Optional[str] = None


class MergeAgentConfig(BaseModel):
    """Configuration for the automated merge-conflict resolution agent.

    When enabled, a merge agent attempts to resolve conflicts that arise
    when merging a task branch into its spec branch.  Only clearly
    mechanical conflicts (generated configs, non-overlapping hunks,
    whitespace) are auto-resolved; ambiguous conflicts (product-code
    overlap) are escalated as blocking questions.

    Default First: enabled with the cheapest capable agent so the common
    generated-config collision case (the overwhelming majority — see F24)
    never blocks the operator.
    """

    enabled: bool = True
    agent: str = "claude"
    model: Optional[str] = None


class AdvisorConfig(BaseModel):
    """Configuration for the advisor consult-when-stuck trial.

    Trial (routing-and-cost bucket): a cheap executor that hits a
    genuinely stuck point mid-task (failing the same test twice, or an
    architecture choice with stated uncertainty) may consult a strong
    model a CAPPED number of times instead of grinding or failing into
    rework. Disabled by default; opt in per-board via config. Only agents
    listed in ``trial_agents`` get the consult protocol injected into
    their prompt, even when ``enabled`` is True.
    """

    enabled: bool = False
    agent: str = "claude"
    model: str = "claude-sonnet-5"
    max_consults: int = 2
    trial_agents: List[str] = Field(default_factory=lambda: ["glm", "minimax"])


class OdinConfig(BaseModel):
    """Top-level Odin configuration."""

    base_agent: str = "claude"
    base_model: Optional[str] = None
    forced_base_provider: Optional[str] = None
    forced_base_model: Optional[str] = None
    agents: Dict[str, AgentConfig] = Field(default_factory=dict)
    model_routing: List[ModelRoute] = Field(default_factory=list)
    banned_models: List[str] = Field(default_factory=list)
    task_storage: str = ".odin/tasks"
    log_dir: str = ".odin/logs"
    cost_storage: str = ".odin/costs"
    config_source: Optional[str] = None
    board_backend: str = "taskit"
    taskit: Optional[TaskItConfig] = Field(default_factory=TaskItConfig)
    chrome_devtools: Optional[ChromeDevToolsConfig] = None
    quota_threshold: int = 80
    max_concurrency: int = 4
    mcps: List[str] = Field(default_factory=lambda: ["taskit", "mobile", "chrome-devtools"])
    execution_timeout_seconds: int = 1800
    # Optional per-task agentic step budget. When set, it is passed to the
    # coding CLI as a turn cap (Claude Code `--max-turns`) so a task cannot
    # burn 100+ model round-trips on mechanical work. None = unbounded (the
    # wall-clock timeout is the only bound), preserving prior behavior.
    max_turns: Optional[int] = None
    worktree_enabled: bool = True
    base_branch: str = "main"
    worktree_dir: str = ".odin/worktrees"
    worktree_post_hooks: List[str] = Field(default_factory=list)
    worktree_symlinks: List[str] = Field(default_factory=list)
    auto_finalize: bool = True
    merge_agent: Optional[MergeAgentConfig] = Field(default_factory=MergeAgentConfig)
    advisor: Optional[AdvisorConfig] = Field(default_factory=AdvisorConfig)
    # Relative path to a durable per-project notes file (e.g.
    # "docs/PROJECT_NOTES.md") injected into every task's effective input
    # after the brief. None resolves to the default "PROJECT_NOTES.md" at
    # the project root.
    project_notes_path: Optional[str] = None

    def enabled_agents(self) -> Dict[str, AgentConfig]:
        return {k: v for k, v in self.agents.items() if v.enabled}
