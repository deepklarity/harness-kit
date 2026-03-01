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
    extras: Dict[str, Any] = Field(default_factory=dict)


class TaskStatus(str, Enum):
    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    TESTING = "testing"
    DONE = "done"
    FAILED = "failed"


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

    agent: str   # e.g. "qwen", "gemini", "glm"
    model: str   # e.g. "qwen3-coder", "GLM-4.7"


class ChromeDevToolsConfig(BaseModel):
    """Configuration for chrome-devtools MCP server."""

    headless: bool = True


class TaskItConfig(BaseModel):
    """Configuration for connecting to a TaskIt instance."""

    base_url: str = "http://localhost:8000"
    board_id: int = 1
    created_by: str = "odin@harness.kit"
    trial_board_name: str = "odin-trial"
    # Auth credentials (loaded from env: ODIN_ADMIN_USER, ODIN_ADMIN_PASSWORD)
    admin_email: Optional[str] = None
    admin_password: Optional[str] = None


class OdinConfig(BaseModel):
    """Top-level Odin configuration."""

    base_agent: str = "claude"
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

    def enabled_agents(self) -> Dict[str, AgentConfig]:
        return {k: v for k, v in self.agents.items() if v.enabled}
