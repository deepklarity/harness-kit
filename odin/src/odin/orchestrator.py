"""Core orchestration engine."""

import asyncio
import json
import os
import random
import re
import shutil
import traceback
import time
from pathlib import Path

import httpx
from typing import Any, Callable, Dict, List, Optional, Tuple

from odin import advisor
from odin.config import load_config
from odin.cost_tracking import CostStore, CostTracker
from odin.dependencies import DepStatus, check_deps, get_failed_deps, get_unmet_deps
from odin.agent_routing import (
    DEFAULT_MIN_SAMPLES,
    DEFAULT_SUCCESS_THRESHOLD,
    AgentStats as AgentRoutingStats,
    StaticFallback as RoutingStaticFallback,
    RoutingDecision,
    cheapest_viable_tier,
    stats_from_dicts,
    suggest_routing,
)
from odin.harnesses import get_harness
from odin.harnesses.base import extract_stream_summary, extract_text_from_stream, extract_token_usage, format_stream_summary_note, stream_json_is_complete, validate_odin_status, validate_odin_status_full
from odin.harnesses.base import (
    OUTCOME_INFRA,
    OUTCOME_TRUNCATED_RESUMABLE,
    OUTCOME_UNCONFIRMED,
    PRE_EXECUTION_CONDITIONS,
    RunOutcome,
    _git_has_uncommitted_changes,
    _git_head,
    build_resume_prompt,
    decide_run_outcome,
    extract_text_from_stream,
    extract_token_usage,
    finish_reason_is_output_cap,
    stream_json_is_complete,
    summarize_worktree_state,
    validate_odin_status,
    validate_odin_status_full,
    worktree_has_resumable_work,
    worktree_has_work_since,
)
from odin.interactive import InteractivePlanSession
from odin.logging import OdinLogger, setup_logger, TaskContextAdapter
from odin.models import AgentConfig, CostTier, OdinConfig, TaskResult
from odin.specs import SpecArchive, SpecStore, generate_spec_id, spec_short_tag
from odin.taskit import TaskManager
from odin.taskit.manager import BackendUnreachable
from odin.taskit.models import Task, TaskStatus
from odin.warm_start import format_warm_start_section, suggest_warm_start_docs
from odin.project_notes import read_project_notes
from odin import tmux

QUOTA_PROVIDER_MAP = {
    "claude": "claude_code",
    "codex": "codex",
    "gemini": "gemini",
    "minimax": "minimax",
    "glm": "glm",
}

# Default CLI binary per agent — mirrors each harness's ``cli_command or
# "<default>"`` resolution so the pre-flight check can run without
# instantiating a harness.  Used by :func:`_resolve_agent_cli`.
_AGENT_DEFAULT_CLI: dict[str, str] = {
    "claude": "claude",
    "codex": "codex",
    "gemini": "gemini",
    "glm": "opencode",
    "minimax": "opencode",
    "agy": "agy",
}


def _resolve_agent_cli(agent_name: str, cfg: AgentConfig) -> Optional[str]:
    """The CLI binary a harness will invoke for *agent_name*.

    Mirrors the ``self._cli = config.cli_command or "<default>"`` line at
    the top of each harness class.  Returns ``None`` only when the agent
    name is unrecognised AND no ``cli_command`` override is set.
    """
    return cfg.cli_command or _AGENT_DEFAULT_CLI.get(agent_name, agent_name)


def _list_available_agents(config: OdinConfig) -> list[str]:
    """Agent names whose CLI binary is installed on PATH.

    Used to build the helpful "available alternatives" list in the
    pre-flight error message so a single-provider user sees what they CAN
    use, not just what's missing.
    """
    available = []
    for name, cfg in config.agents.items():
        if not cfg.enabled:
            continue
        binary = _resolve_agent_cli(name, cfg)
        if binary and shutil.which(binary):
            available.append(name)
    return available

# Payload truncation limits for task execution_result posts.
PAYLOAD_EFFECTIVE_INPUT_LIMIT = 5000
PAYLOAD_ERROR_MESSAGE_LIMIT = 2000
# How many chars to preserve from the end of a JSONL trace when truncating.
# Agent CLIs (Claude Code, Codex) put usage summaries as the last line(s).
TRACE_TAIL_PRESERVE = 2000


def _truncate_trace(raw: str, limit: int) -> str:
    """Truncate a JSONL trace while preserving the tail.

    Agent CLIs put usage summaries (modelUsage, turn.completed, step_finish)
    as the last line(s) of JSONL output. Naive front-truncation ([:limit])
    discards the end, losing token counts and cost data.

    Strategy: keep the first (limit - TRACE_TAIL_PRESERVE) chars and the
    last TRACE_TAIL_PRESERVE chars, joined on a newline boundary.
    """
    if len(raw) <= limit:
        return raw
    head_budget = limit - TRACE_TAIL_PRESERVE
    # Find a clean newline boundary for the head
    head_end = raw.rfind("\n", 0, head_budget)
    if head_end == -1:
        head_end = head_budget
    # Find a clean newline boundary for the tail
    tail_start = raw.rfind("\n", len(raw) - TRACE_TAIL_PRESERVE)
    if tail_start == -1:
        tail_start = len(raw) - TRACE_TAIL_PRESERVE
    else:
        tail_start += 1  # skip the newline itself
    return raw[:head_end] + "\n" + raw[tail_start:]


def _comment_attr(comment, name, default=""):
    """Access a comment attribute uniformly whether it's a dict or object."""
    if isinstance(comment, dict):
        return comment.get(name, default)
    return getattr(comment, name, default)


# Noise patterns consolidated from reflection.py comment filtering
_NOISE_LINE_PATTERNS = (
    "DeprecationWarning:", "YOLO mode", "Loaded cached credentials",
    "Loading extension:", "supports tool updates", "--trace-deprecation",
    "(node:", "Server '",
    "Ripgrep is not available. Falling back to GrepTool.",
    "Error executing tool mcp",
    "MCP tool 'newpage' reported an error",
    "MCP tool 'listpages' reported an error",
    "is ignored by configured ignore patterns",
)


def _filter_comment_content(content: str) -> str:
    """Filter noise from a comment's content, returning cleaned text.

    Applies two levels of filtering:
    1. Whole-comment skip: returns "" for system-injected debug content
       ("Effective input" prefix, raw JSON system events).
    2. Line-level skip: strips raw JSON stream lines and CLI noise
       (DeprecationWarning, YOLO mode, etc.).
    """
    if not content:
        return ""
    # Whole-comment skip patterns
    if content.startswith("Effective input"):
        return ""
    if '{"type":"system"' in content:
        return ""

    # Line-level filtering
    clean_lines = []
    for ln in content.splitlines():
        stripped = ln.strip()
        # Skip raw JSON stream lines
        if stripped.startswith("{") and stripped.endswith("}"):
            continue
        # Skip CLI noise patterns
        if any(noise in stripped for noise in _NOISE_LINE_PATTERNS):
            continue
        clean_lines.append(ln)
    return "\n".join(clean_lines).strip()


# Default CDP endpoint a microsandbox guest connects to when chrome-devtools is
# enabled. 127.0.0.1 is rewritten to the host LAN IP when the MCP config is staged
# into the guest (see microsandbox.py::_stage_mcp_config), so this resolves to the
# host's :9222 — where the operator runs a shared headless browser.
DEFAULT_MICROSANDBOX_CDP_URL = "http://127.0.0.1:9222"


def _microsandbox_browser_url(config) -> str:
    """CDP endpoint the in-guest chrome-devtools-mcp connects to under microsandbox.

    Honors an explicit ``chrome_devtools.browser_url`` override, else falls back to
    the default host :9222 (Default First — works once the operator starts a browser).
    """
    cd = getattr(config, "chrome_devtools", None)
    override = getattr(cd, "browser_url", None) if cd else None
    return override or DEFAULT_MICROSANDBOX_CDP_URL


class WorktreeIsolationError(RuntimeError):
    """Worktree creation failed for a task that requires git isolation.

    Raised instead of falling back to project-root execution: a non-isolated
    agent edits and commits on the operator's branch (F44/F49/F51).
    """


class SuggestedAgentDisabled(RuntimeError):
    """Raised when a planner-named ``suggested_agent`` is not on the roster (W10.4).

    Mirrors the dispatch-side ``tasks.dag_executor.AgentNotEnabledOnBoard``
    so the operator sees the same single-line message whether the
    trigger is the planner (this class) or the dispatcher / manual
    assign (the dag_executor exception). Carries ``agent`` and
    ``settings_url`` so the message format is identical at both layers
    and the operator knows exactly which switch to flip.

    Why this is raised instead of a silent fallback: the
    ``_route_task_api`` Phase 1 used to skip ``suggested`` if it wasn't
    in ``routing_config["agents"]`` (the endpoint filters roster-disabled
    agents out), then Phase 2 picked the cheapest viable tier. The
    operator saw ``assignee=claude`` with no indication that the planner
    had asked for ``gemini``. Board 6 surfaced this — the cheapest-tier
    fallback masked the real cause (the operator had disabled gemini
    on that board) until hours later.
    """

    def __init__(self, *, agent, settings_url):
        self.agent = agent
        self.settings_url = settings_url
        super().__init__(self._message())

    def _message(self):
        if self.settings_url:
            return (
                f"Planner named agent '{self.agent}' but it is not "
                f"enabled on this board's roster. Enable it at "
                f"{self.settings_url} or update the plan."
            )
        return (
            f"Planner named agent '{self.agent}' but it is not in the "
            f"active lineup. Add the agent to the lineup or update the "
            f"plan to name a roster-enabled agent."
        )


class Orchestrator:
    """Task-board orchestration engine.

    Reads a spec, decomposes it into tasks (like a Trello/Asana board),
    dispatches tasks to agents, and tracks progress. Tasks evolve — they
    can be reassigned, commented on, and their purpose can shift over time.

    Spec status is derived from tasks — no stored status, no sync bugs.
    Multiple specs coexist on the same board.

    Supports both staged workflow (plan → exec) and convenience run().
    """

    def __init__(self, config: Optional[OdinConfig] = None, trial: bool = False):
        self.config = config or load_config()
        self.logger = OdinLogger(self.config.log_dir)
        self._log = TaskContextAdapter(
            setup_logger("odin.orchestrator", log_dir=self.config.log_dir)
        )
        # Spec storage lives alongside task storage
        spec_dir = str(Path(self.config.task_storage).parent / "specs")
        self.spec_store = SpecStore(spec_dir)

        # Board backend (local disk by default, or taskit/jira via config)
        self._backend = None
        if self.config.board_backend != "local":
            from odin.backends.registry import get_backend
            backend_kwargs = {"task_storage": self.config.task_storage, "spec_storage": spec_dir}
            if self.config.board_backend == "taskit" and self.config.taskit:
                backend_kwargs.update(self.config.taskit.model_dump())
            self._backend = get_backend(self.config.board_backend, **backend_kwargs)

        # Switch to trial board when requested
        if trial and self._backend and hasattr(self._backend, "use_trial_board"):
            trial_name = "odin-trial"
            if self.config.taskit:
                trial_name = self.config.taskit.trial_board_name
            self._backend.use_trial_board(trial_name)

        self.task_mgr = TaskManager(self.config.task_storage, backend=self._backend)

        # Spec backend: delegate to board backend when available
        self._spec_backend = self._backend

        # Worktree manager (conditional on config)
        self._worktree = None
        self._worktree_disabled_reason = None
        self._worktree_project_root = None
        if self.config.worktree_enabled:
            try:
                from odin.worktree import WorktreeManager
                project_root = Path(self.config.task_storage).resolve().parent.parent
                self._worktree_project_root = project_root
                self._worktree = WorktreeManager(
                    project_root,
                    worktree_dir=self.config.worktree_dir,
                )
            except ValueError as exc:
                self._worktree_disabled_reason = str(exc)
                self._log.warning("WorktreeManager init failed (no git repo): %s", exc)
            except Exception:
                self._worktree_disabled_reason = "unexpected error during WorktreeManager init"
                self._log.warning("Failed to initialize WorktreeManager, worktree isolation disabled", exc_info=True)

        # Cost tracking — load pricing table for cost estimation
        self.cost_store = CostStore(self.config.cost_storage)
        pricing = self._load_pricing_table()
        self.cost_tracker = CostTracker(self.cost_store, pricing=pricing)
        # Availability cache to avoid redundant is_available() calls during planning
        self._availability_cache: Dict[str, bool] = {}
        # Test injection hook: when set, exec_task uses this instead of a
        # real strong-model harness call for advisor consults. None (the
        # default) wires the real odin.advisor.consult_advisor() call.
        self._advisor_consult_override: Optional[advisor.ConsultFn] = None

        # Startup orphan-sandbox sweep flag. The sweep itself is NOT run in
        # __init__ — constructors must not do subprocess I/O, and the
        # orchestrator is built for every CLI verb (plan/status/specs/exec),
        # so an eager sweep here would spawn ``msb sandbox list`` on read-only
        # paths and (per the task-154 review) risk destabilizing the executor.
        # It runs lazily, once per process, on the first real ``exec_task``
        # (see :meth:`_maybe_sweep_orphaned_sandboxes`).
        self._startup_sweep_done: bool = False

    def _maybe_sweep_orphaned_sandboxes(self, *, force: bool = False) -> None:
        """Backstop for crashed runs: sweep leftover ``odin-msb-*`` sandboxes.

        Runs at most once per process (on the first ``exec_task``), so it only
        fires at executor cold start — before THIS process has minted any
        sandbox of its own — and never races a live in-process run. The
        per-run finally in ``MicrosandboxHarness._execute_sync`` is the
        primary cleanup; this only catches what a crash left behind.

        Best-effort and fully wrapped: a sweep failure can never gate
        execution. Cross-process concurrency (another worker mid-run) is an
        accepted limitation — ``odin gc --prune`` is the operator-controlled,
        guaranteed-safe path.
        """
        if self._startup_sweep_done and not force:
            return
        self._startup_sweep_done = True
        try:
            from odin.harnesses.microsandbox import MicrosandboxHarness
            removed = MicrosandboxHarness.sweep_startup_orphans()
            if removed:
                self._log.info(
                    "Swept %d orphaned microsandbox sandbox(es) from previous runs: %s",
                    len(removed), ", ".join(removed),
                )
        except Exception as exc:
            # The sweep is a backstop, not a gate — a stale cleanup must
            # never block the executor from running.
            self._log.debug("Startup sandbox sweep skipped: %s", exc)

    def _ensure_git_repo(self) -> bool:
        """Lazy auto-init git repo when worktree is enabled but no .git exists.

        Returns True if worktree is now available, False otherwise.
        """
        if self._worktree is not None:
            return True
        if not self.config.worktree_enabled or not self._worktree_project_root:
            return False

        project_root = self._worktree_project_root

        # Maybe .git appeared since __init__ (another process created it)
        if (project_root / ".git").exists():
            try:
                from odin.worktree import WorktreeManager
                self._worktree = WorktreeManager(
                    project_root, worktree_dir=self.config.worktree_dir,
                )
                self._worktree_disabled_reason = None
                self._log.info("Git repo detected on retry, worktree enabled")
                return True
            except Exception:
                self._log.warning("WorktreeManager retry failed", exc_info=True)
                return False

        # Auto-init: create git repo
        import subprocess as _sp
        try:
            self._log.info("Auto-initializing git repo at %s", project_root)
            _sp.run(
                ["git", "init", "-b", "main"],
                cwd=project_root, check=True, capture_output=True,
            )
            gitignore = project_root / ".gitignore"
            if not gitignore.exists():
                gitignore.write_text(
                    "# Odin internals\n"
                    ".odin/worktrees/\n"
                    ".odin/locks/\n"
                    ".odin/logs/\n"
                    ".odin/costs/\n"
                    ".env\n"
                )
            _sp.run(
                ["git", "add", "-A"],
                cwd=project_root, check=True, capture_output=True,
            )
            _sp.run(
                ["git", "commit", "-m", "Initial commit (odin auto-init)"],
                cwd=project_root, check=True, capture_output=True,
            )
            from odin.worktree import WorktreeManager
            self._worktree = WorktreeManager(
                project_root, worktree_dir=self.config.worktree_dir,
            )
            self._worktree_disabled_reason = None
            self._log.info("Auto-initialized git repo and enabled worktree")
            return True
        except Exception as exc:
            self._worktree_disabled_reason = f"Auto-init failed: {exc}"
            self._log.warning("Failed to auto-init git repo: %s", exc)
            return False

    def _forced_provider(self) -> Tuple[Optional[str], Optional[str]]:
        return self.config.forced_base_provider, self.config.forced_base_model

    def _forced_enabled(self) -> bool:
        provider, _ = self._forced_provider()
        return bool(provider)

    def _planning_agent_model(self) -> Tuple[str, Optional[str]]:
        provider, model = self._forced_provider()
        if provider:
            return provider, model
        base_name = self.config.base_agent
        base_cfg = self.config.agents.get(base_name)
        if not base_cfg:
            raise RuntimeError(f"Base agent '{base_name}' not found in config")
        return base_name, self.config.base_model or base_cfg.premium_model or base_cfg.default_model

    def _assert_agent_cli_available(self, agent_name: str, *, action: str) -> None:
        """Pre-flight capability check: clear error if the agent's CLI is missing.

        A single-provider user (e.g. codex-only) whose ``base_agent`` defaults
        to a provider they don't have gets a plain message naming the fix —
        not a deep subprocess crash.  Called at planning, execution, and
        summarize dispatch points.

        Skipped for the built-in ``mock`` harness (test fixture, no CLI).
        """
        if agent_name == "mock":
            return
        cfg = self.config.agents.get(agent_name)
        if not cfg:
            available = _list_available_agents(self.config)
            raise RuntimeError(
                f"Agent '{agent_name}' is not configured (needed for {action}). "
                f"Available agents: {', '.join(available) or 'none'}. "
                f"Set the relevant agent in .odin/config.yaml, "
                f"or run `odin doctor` for the full diagnostic."
            )
        binary = _resolve_agent_cli(agent_name, cfg)
        if binary and shutil.which(binary):
            return
        available = _list_available_agents(self.config)
        avail_str = ", ".join(available) if available else "none"
        if action == "planning":
            config_hint = "Set base_agent to an available agent in .odin/config.yaml"
        elif action == "summarize":
            config_hint = "Set base_agent to an available agent in .odin/config.yaml"
        else:
            config_hint = "Reassign the task to an available agent"
        raise RuntimeError(
            f"Agent '{agent_name}' CLI '{binary or agent_name}' is not installed "
            f"(needed for {action}). "
            f"Available: {avail_str}. {config_hint}, "
            f"or run `odin doctor` for the full diagnostic."
        )

    @staticmethod
    def _load_pricing_table() -> Optional[Dict]:
        """Attempt to load model pricing from agent_models.json.

        Returns None (graceful degradation) if the file isn't found —
        cost estimation will simply be skipped.
        """
        from odin.cost_tracking.estimator import load_pricing_table
        # Look for agent_models.json relative to the taskit backend
        candidates = [
            Path(__file__).resolve().parents[3] / "taskit" / "taskit-backend" / "data" / "agent_models.json",
        ]
        for path in candidates:
            if path.exists():
                try:
                    return load_pricing_table(str(path))
                except Exception:
                    return None
        return None

    def _make_advisor_consult_fn(self, advisor_cfg, working_dir: str) -> "advisor.ConsultFn":
        """Build the (question, task_title) -> ConsultResult callable for AdvisorWatcher.

        Tests set ``self._advisor_consult_override`` to avoid a real
        strong-model harness call; production wires the real one-shot
        consult via ``odin.advisor.consult_advisor``.
        """
        if self._advisor_consult_override is not None:
            return self._advisor_consult_override

        agent_cfg = self.config.agents.get(advisor_cfg.agent) or AgentConfig(
            default_model=advisor_cfg.model,
        )

        async def _consult(question: str, task_title: str) -> "advisor.ConsultResult":
            return await advisor.consult_advisor(
                question,
                task_title,
                agent_cfg=agent_cfg,
                agent=advisor_cfg.agent,
                model=advisor_cfg.model,
                working_dir=working_dir,
            )

        return _consult

    # ------------------------------------------------------------------
    # Spec persistence
    # ------------------------------------------------------------------

    def _save_spec(self, spec: SpecArchive) -> None:
        """Save a spec archive, routing through backend when available."""
        if self._spec_backend:
            self._spec_backend.save_spec(spec)
        self.spec_store.save(spec)

    def _update_spec_metadata(self, spec_id: str, updates: Dict[str, Any]) -> None:
        """Update specific metadata fields on a spec (local + backend)."""
        spec_obj = self.spec_store.load(spec_id)
        if not spec_obj:
            self._log.warning("Cannot update metadata — spec %s not found", spec_id)
            return
        spec_obj.metadata.update(updates)
        self._save_spec(spec_obj)

    def finalize_spec(self, spec_id: str) -> Optional[str]:
        """Finalize a spec: clean up worktrees, create PR.

        Returns the PR URL on success, None otherwise.
        """
        if not self._worktree:
            self._log.info("Worktree not enabled, skipping finalization for %s", spec_id)
            return None

        spec_obj = self.spec_store.load(spec_id)
        if not spec_obj:
            self._log.warning("Spec %s not found for finalization", spec_id)
            return None

        # Clean up worktrees
        self._worktree.finalize_spec(spec_id)

        # Gather task summaries for PR body
        tasks = self.task_mgr.list_tasks(spec_id=spec_id)
        task_summaries = [
            f"#{t.id[:8]} — {t.title} ({t.status.value})"
            for t in tasks
        ]

        # Create PR
        pr_url = self._worktree.create_spec_pr(
            spec_id, spec_obj.title, task_summaries
        )

        # Update spec metadata only when PR was actually created
        if pr_url:
            from datetime import datetime, timezone
            updates: Dict[str, Any] = {
                "finalized_at": datetime.now(timezone.utc).isoformat(),
                "pr_url": pr_url,
            }
            self._update_spec_metadata(spec_id, updates)

            # Transition all TESTING tasks to DONE in the backend
            if self._backend and hasattr(self._backend, "finalize_spec_tasks"):
                self._backend.finalize_spec_tasks(spec_id, pr_url)

        self._log.info("Finalized spec %s: pr_url=%s", spec_id, pr_url)
        return pr_url

    def _save_plan_json(self, spec_id: str, sub_tasks: List[Dict[str, Any]]) -> Path:
        """Write plan sub-tasks JSON to .odin/plans/ for auditability.

        In normal flow the agent writes this file directly.  This method
        exists for programmatic callers that bypass the agent (e.g. tests)
        and need to persist a plan to the canonical location.
        """
        plans_dir = Path(self.config.task_storage).parent / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        plan_path = plans_dir / f"plan_{spec_id}.json"
        plan_path.write_text(json.dumps(sub_tasks, indent=2))
        self._log.info("Plan JSON saved: %s", plan_path)
        return plan_path

    # ------------------------------------------------------------------
    # plan()
    # ------------------------------------------------------------------

    async def plan(
        self,
        spec: str,
        working_dir: Optional[str] = None,
        spec_file: Optional[str] = None,
        mode: str = "quiet",
        stream_callback: Optional[Callable[[str], None]] = None,
        quick: bool = False,
        skip_reflection: bool = False,
        direct: bool = False,
        gate: bool = True,
        gate_callback: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None,
    ) -> Tuple[str, List[Task]]:
        """Decompose a spec into sub-tasks and create them with suggested agent
        assignments.  Does NOT execute anything.

        All modes use the same unified prompt built by ``_build_plan_prompt()``.
        The agent writes its plan JSON to ``plan_path`` on disk — structured
        data never flows through the terminal.  Modes differ only in UX
        wrapper (tmux vs streaming vs spinner).

        Args:
            mode: Execution mode — "interactive" (tmux), "auto" (streaming),
                or "quiet" (spinner).
            stream_callback: Called per chunk in "auto" mode for terminal display.
            quick: If True, instruct the LLM to skip codebase exploration.

        Returns a (spec_id, tasks) tuple.
        """
        wd = working_dir or str(Path.cwd())
        self._log.info(
            "Plan started: spec_file=%s, spec_length=%d, mode=%s, quick=%s",
            spec_file, len(spec), mode, quick,
        )
        self.logger.log(action="plan_started", metadata={"spec_length": len(spec)})

        # 1. Create spec archive FIRST — spec_id is available for plan_path
        title = spec_file or _extract_title(spec)
        sid = generate_spec_id(title)
        self._last_plan_spec_id = sid  # Track for mark_planning_failed()
        spec_archive = SpecArchive(
            id=sid,
            title=title,
            source=spec_file or "inline",
            content=spec,
            metadata={"working_dir": wd},
        )
        self._save_spec(spec_archive)

        # 1b. Create spec branch for worktree isolation (best-effort)
        if self._worktree:
            try:
                branch = self._worktree.create_spec_branch(
                    sid, base_branch=self.config.base_branch,
                )
                spec_archive.metadata["branch"] = branch
                self._save_spec(spec_archive)
                self._log.info("Created spec branch: %s", branch)

                # Create a spec-level worktree so the "Open in editor" link
                # points at the spec branch code (not a task branch).
                try:
                    spec_wt_path = self._worktree.create_spec_worktree(
                        sid,
                        post_hooks=self.config.worktree_post_hooks,
                        symlinks=self.config.worktree_symlinks,
                    )
                    spec_archive.metadata["worktree_path"] = str(spec_wt_path)
                    self._save_spec(spec_archive)
                    self._log.info("Created spec worktree: %s", spec_wt_path)
                except Exception:
                    self._log.warning("Failed to create spec worktree for %s", sid, exc_info=True)
            except Exception:
                self._log.warning("Failed to create spec branch for %s, continuing without worktree isolation", sid, exc_info=True)

        # 2. Derive plan_path — agent writes plan JSON here
        plans_dir = Path(self.config.task_storage).parent / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        plan_path = (plans_dir / f"plan_{sid}.json").resolve()

        # 3. Fetch quota data (graceful degradation if unavailable)
        quota = await self._fetch_quota()
        if quota:
            self.logger.log(
                action="quota_fetched",
                metadata={name: data for name, data in quota.items()},
            )

        # 3b. Fetch routing config from API (agent/model/tier data)
        routing_config = self._fetch_routing_config()

        # 4. Build unified plan prompt with plan_path baked in
        available_agents = await self._build_available_agents(quota, routing_config)

        # 4.5. Clarification gate — runs BEFORE task breakdown in ALL modes.
        # The clarification is always a separate one-shot dispatch that writes
        # a clarification JSON (questions + summary) and an HTML preview page.
        # gate_callback then surfaces them to the human and collects a nod.
        # Only after the nod does task breakdown begin — interactive mode
        # opens its tmux session, auto/quiet runs the decomposition dispatch.
        clarification_path = plans_dir / f"clarification_{sid}.json"
        preview_path = plans_dir / f"preview_{sid}.html"

        if gate:
            clar_prompt = self._build_clarification_prompt(
                spec=spec,
                clarification_path=str(clarification_path),
                preview_path=str(preview_path),
                quick=quick,
            )
            try:
                await self._decompose(
                    clar_prompt, wd, spec_id=sid,
                    stream_callback=stream_callback,
                    plan_path=str(clarification_path),
                )
            except Exception:
                self._log.warning(
                    "Clarification gate dispatch failed, proceeding without gate",
                    exc_info=True,
                )

            clarification: Dict[str, Any] = {"questions": [], "summary": ""}
            if clarification_path.exists():
                try:
                    parsed = json.loads(clarification_path.read_text())
                    if isinstance(parsed, dict):
                        clarification = parsed
                    else:
                        self._log.warning(
                            "Clarification file is not a JSON object, proceeding without gate"
                        )
                except (json.JSONDecodeError, OSError):
                    self._log.warning(
                        "Failed to parse clarification JSON, proceeding without gate"
                    )

            clarification["preview_path"] = str(preview_path)
            clarification["preview_exists"] = preview_path.exists()

            if gate_callback:
                answers = gate_callback(clarification)
                if answers is None:
                    raise RuntimeError(
                        "Planning aborted at clarification gate."
                    )
                if answers:
                    spec = spec + f"\n\n---\n## Clarification Answers\n{answers}"

            self.logger.log(action="clarification_gate_complete", metadata={
                "question_count": len(clarification.get("questions", [])),
                "has_answers": bool(answers) if gate_callback else False,
            })

        prompt = self._build_plan_prompt(
            spec=spec,
            plan_path=str(plan_path),
            available_agents=available_agents,
            quota=quota,
            quick=quick,
        )

        # 5. Dispatch to harness based on mode
        import time as _time
        decompose_result: Optional[TaskResult] = None
        log_dir = Path(self.config.log_dir)
        trace_file = str(log_dir / f"plan_{sid}.trace.jsonl")
        if mode == "interactive":
            t0 = _time.monotonic()
            transcript_path = self._run_interactive_plan(prompt, wd, quick=quick, direct=direct)
            elapsed_ms = (_time.monotonic() - t0) * 1000
            # Read transcript for trace capture
            raw_output = ""
            if transcript_path and Path(transcript_path).exists():
                raw_output = Path(transcript_path).read_text(errors="replace")
                raw_output = self._clean_interactive_transcript(raw_output)
            decompose_result = TaskResult(
                success=True,
                output=raw_output,
                duration_ms=elapsed_ms,
                agent=self.config.base_agent,
            )
        else:
            decompose_result = await self._decompose(
                prompt, wd, spec_id=sid, stream_callback=stream_callback, plan_path=str(plan_path)
            )
            # For auto/quiet modes, read the trace file written by _decompose
            if Path(trace_file).exists():
                decompose_result = TaskResult(
                    success=decompose_result.success,
                    output=Path(trace_file).read_text(),
                    duration_ms=decompose_result.duration_ms,
                    agent=decompose_result.agent,
                    error=decompose_result.error,
                )

        self.logger.log(
            action="decompose_dispatched",
            metadata={"mode": mode},
        )

        # 6. Read plan from disk — agent wrote JSON to plan_path
        if not plan_path.exists():
            raise RuntimeError(
                f"Planning agent did not write plan to {plan_path}. "
                f"Check the agent output for errors."
            )
        sub_tasks = self._parse_json_array(plan_path.read_text())
        self._log.info(
            "Plan read from disk: %d sub-tasks from spec %s",
            len(sub_tasks), sid,
        )
        self.logger.log(
            action="decomposition_complete",
            metadata={"sub_task_count": len(sub_tasks)},
        )

        # 7. Create tasks from plan
        tasks = await self._create_tasks_from_plan(sub_tasks, sid, quota, routing_config, skip_reflection=skip_reflection)

        # 8. Post planning trace to backend (if captured)
        if decompose_result is not None:
            self._record_planning_trace(sid, decompose_result, prompt)

        # 9. Mark spec as planning_complete
        self._mark_planning_complete(sid)

        self._log.info("Plan completed: spec_id=%s, task_count=%d", sid, len(tasks))
        self.logger.log(
            action="plan_completed",
            metadata={"spec_id": sid, "task_count": len(tasks)},
        )
        return sid, tasks

    def _record_planning_trace(
        self,
        spec_id: str,
        result: TaskResult,
        effective_input: str,
    ) -> None:
        """Post the planning trace to the spec backend (best-effort)."""
        backend = getattr(self.task_mgr, "_backend", None)
        if backend is None:
            # Local-only mode — store in spec archive metadata
            spec_store = SpecStore(
                str(Path(self.config.task_storage).parent / "specs")
            )
            spec_archive = spec_store.load(spec_id)
            if spec_archive:
                meta = spec_archive.metadata or {}
                meta["planning_trace"] = {
                    "agent": result.agent or self.config.base_agent,
                    "duration_ms": result.duration_ms,
                    "success": result.success,
                    "output_length": len(result.output or ""),
                }
                spec_archive.metadata = meta
                spec_store.save(spec_archive)
            return

        # Backend available — POST to /specs/:id/planning_result/
        try:
            agent_name, model = self._planning_agent_model()
            backend.record_planning_result(
                spec_id=spec_id,
                raw_output=result.output or "",
                duration_ms=result.duration_ms or 0,
                agent=result.agent or agent_name,
                model=model,
                effective_input=effective_input[:5000],
                success=result.success,
            )
        except Exception:
            self._log.warning(
                "Failed to post planning trace for spec %s",
                spec_id, exc_info=True,
            )

    def _mark_planning_complete(self, spec_id: str) -> None:
        """Transition spec status to planning_complete (best-effort)."""
        backend = getattr(self.task_mgr, "_backend", None)
        if backend is None:
            return
        try:
            backend.mark_planning_complete(spec_id)
        except Exception:
            self._log.warning(
                "Failed to mark spec %s as planning_complete",
                spec_id, exc_info=True,
            )

    def mark_planning_failed(self) -> None:
        """Mark the last planned spec as planning_failed (called by CLI on crash)."""
        spec_id = getattr(self, "_last_plan_spec_id", None)
        if not spec_id:
            return
        backend = getattr(self.task_mgr, "_backend", None)
        if backend is None:
            return
        try:
            backend.mark_planning_failed(spec_id)
        except Exception:
            self._log.warning(
                "Failed to mark spec %s as planning_failed",
                spec_id, exc_info=True,
            )

    def _record_warm_start_docs(
        self, task_id: str, suggestions: List[Dict[str, Any]], *, mock: bool,
    ) -> None:
        """Persist suggested warm-start docs onto task metadata (best-effort).

        The wave-6 audit cross-references this against the harness trace's
        file-read events to measure whether agents actually open the docs they
        were told to read first. Never gates execution — every failure path
        degrades silently. Skipped under mock mode (no backend writes).
        """
        if mock:
            return
        records = [
            {"path": s.get("path"), "reason": s.get("reason"), "score": s.get("score")}
            for s in (suggestions or [])
        ]
        if not records:
            return
        try:
            task = self.task_mgr.get_task(task_id)
            if not task:
                return
            metadata = task.metadata or {}
            metadata["warm_start_docs"] = records
            task.metadata = metadata
            self.task_mgr.update_task(task)
        except Exception:
            self._log.debug(
                "[task:%s] could not record warm_start_docs metadata", task_id, exc_info=True,
            )

    def _fetch_routing_config(self) -> Optional[Dict[str, Any]]:
        """Fetch agent/model routing config from the TaskIt API.

        Returns the routing-config response or None if unavailable.
        Graceful degradation: falls back to config-based routing.
        """
        backend = self._backend
        if backend is None or not hasattr(backend, "fetch_routing_config"):
            return None
        try:
            return backend.fetch_routing_config()
        except Exception:
            self._log.debug("Could not fetch routing config from API", exc_info=True)
            return None

    def _fetch_agent_stats(
        self, *, spec_id: Optional[str] = None
    ) -> Dict[str, AgentRoutingStats]:
        """Fetch per-agent success-rate + median cost from the TaskIt API.

        Returns a ``{agent_name: AgentStats}`` mapping. When the
        backend is the local-disk stub, the endpoint is unavailable,
        or the call fails, returns ``{}`` — the suggester treats an
        empty mapping as thin history and emits a StaticFallback.

        Default First: every failure mode degrades to the existing
        static behavior rather than throwing. ``spec_id`` scopes the
        rollup to a single spec; pass it during plan so the per-task
        pick respects that spec's history specifically.
        """
        backend = self._backend
        if backend is None or not hasattr(backend, "fetch_agent_stats"):
            return {}
        try:
            payload = backend.fetch_agent_stats(spec_id=spec_id) or {}
            rows = payload.get("agents", []) if isinstance(payload, dict) else []
            return stats_from_dicts(rows)
        except Exception:
            self._log.debug("Could not fetch agent stats from API", exc_info=True)
            return {}

    def _agent_default_from_routing_config(self, agent_name: str) -> Optional[str]:
        """Look up an agent's default_model via the TaskIt routing-config API.

        Mirrors the same source ``_route_task_api()`` consults at plan time
        (the ``/boards/{id}/routing-config/`` endpoint backed by
        ``agent_models.json``). Returns None if the API is unavailable, the
        agent is unknown to the lineup, or no default_model is set.

        The result is cached per-process so repeated exec-path lookups for
        different tasks on the same agent don't re-hit the API.
        """
        if not agent_name:
            return None
        cache = getattr(self, "_routing_default_cache", None)
        if cache is None:
            self._routing_default_cache = {}
            cache = self._routing_default_cache
        if agent_name in cache:
            return cache[agent_name]
        config = self._fetch_routing_config()
        if not config:
            cache[agent_name] = None
            return None
        result: Optional[str] = None
        for agent_data in config.get("agents", []) or []:
            if agent_data.get("name") == agent_name:
                result = agent_data.get("default_model") or None
                break
        cache[agent_name] = result
        return result

    def _resolve_task_model(
        self, task: Optional[Any], agent_name: str
    ) -> Optional[str]:
        """Resolve which model string an executing task should use.

        Single source of truth used by both exec paths
        (``_summarize_task`` and ``_execute_task``). Precedence:

        1. ``task.metadata["selected_model"]`` if set by plan routing or the
           TaskIt ``model_name`` field (already merged by the backend).
        2. ``task.model`` if exposed by a backend payload (defensive).
        3. The agent's ``default_model`` from the TaskIt routing-config
           lineup — the same lineup ``_route_task_api()`` consults.
        4. The agent config's ``default_model`` (config-file fallback).
        5. ``None`` — caller passes no model to the harness.

        Each fallback path logs INFO with the resolved value and source.
        """
        task_id = getattr(task, "id", None) if task else None

        # 1. selected_model on task metadata (already set by planner / DB).
        selected: Optional[str] = None
        if task is not None:
            metadata = getattr(task, "metadata", None) or {}
            if isinstance(metadata, dict):
                candidate = metadata.get("selected_model")
                if candidate:
                    selected = candidate
            # 2. Defensive: some backends surface model as a top-level field.
            if not selected:
                candidate = getattr(task, "model", None)
                if candidate:
                    selected = candidate
        if selected:
            return selected

        # 3. TaskIt lineup default (same source plan-time routing uses).
        lineup_default = self._agent_default_from_routing_config(agent_name)
        if lineup_default:
            self._log.info(
                "[task:%s] model resolved from agent default: %s (lineup)",
                task_id or "?", lineup_default,
            )
            return lineup_default

        # 4. Agent config fallback (yaml / built-in default).
        cfg = self.config.agents.get(agent_name) if agent_name else None
        if cfg and cfg.default_model:
            self._log.info(
                "[task:%s] model resolved from agent default: %s (config)",
                task_id or "?", cfg.default_model,
            )
            return cfg.default_model

        return None

    def _record_model_pickup(
        self,
        task: Any,
        dispatch_model: Optional[str],
        agent_name: str,
        reason: str,
        policy: str,
        actor: str,
        changed_by: str,
    ) -> bool:
        """Record that dispatch will use a different model than the task declared.

        F45 mandate #2: any model switch at dispatch MUST be recorded with a
        reason in ``task.metadata`` and a ``TaskHistory`` row that names the
        policy that authorised it. The F45 trigger incident (task #114) was
        a silent model swap from ``minimax/M3`` to ``gemini-3-flash-preview``;
        the operator had no audit trail that said "X policy did this" — so
        the failure looked unmotivated and took multiple cycles to trace.

        The authoritative surfaces checked (in order) for the prior value:

          - ``task.metadata["selected_model"]`` (plan router / TaskIt merge)
          - ``task.model_name`` (operator's authoritative Django column)
          - ``task.model`` (defensive for backends that surface it as an
            attribute rather than metadata)

        If ``dispatch_model`` matches ANY of those, the dispatch is a no-op
        and nothing is written — the false-positive guard against spamming
        the audit trail on every routine re-dispatch.

        Returns True when a change was recorded, False when dispatch matches
        the authoritative values (no-op).
        """
        if not dispatch_model:
            return False
        if task is None:
            return False

        metadata = getattr(task, "metadata", None) or {}
        if not isinstance(metadata, dict):
            metadata = {}

        authoritative = (
            metadata.get("selected_model")
            or getattr(task, "model_name", None)
            or getattr(task, "model", None)
        )
        # When nothing authoritative exists (first dispatch, fresh task),
        # there's nothing to compare against — treat as a no-op so the
        # helper doesn't fabricate history for genuinely new tasks.
        if not authoritative:
            return False
        if str(authoritative) == str(dispatch_model):
            return False

        from datetime import datetime, timezone as _tz

        changed_at = datetime.now(_tz.utc).isoformat()
        change_record = {
            "old": authoritative,
            "new": dispatch_model,
            "reason": reason,
            "policy": policy,
            "at": changed_at,
        }
        metadata["last_model_change"] = change_record
        try:
            task.metadata = metadata
        except Exception:
            self._log.debug(
                "[task:%s] could not mutate task.metadata for pickup history",
                getattr(task, "id", "?"),
                exc_info=True,
            )

        # First-class history row (the contract surface F45 mandate #2 demands).
        if hasattr(self.task_mgr, "add_history"):
            try:
                self.task_mgr.add_history(
                    task_id=getattr(task, "id", None),
                    field_name="model",
                    old_value=str(authoritative),
                    new_value=str(dispatch_model),
                    changed_by=changed_by,
                    reason=reason,
                    policy=policy,
                )
            except Exception:
                self._log.debug(
                    "[task:%s] task_mgr.add_history raised; recording change "
                    "in metadata only",
                    getattr(task, "id", "?"),
                    exc_info=True,
                )

        # STATUS_UPDATE comment summarising the change for human reviewers.
        if hasattr(self.task_mgr, "add_comment"):
            try:
                self.task_mgr.add_comment(
                    task_id=getattr(task, "id", None),
                    author=actor or "odin",
                    content=(
                        f"Model change for dispatch ({agent_name}): "
                        f"{authoritative} → {dispatch_model}. "
                        f"Reason: {reason}. Policy: {policy}."
                    ),
                    comment_type="STATUS_UPDATE",
                )
            except Exception:
                self._log.debug(
                    "[task:%s] task_mgr.add_comment raised; history row + "
                    "metadata are still authoritative",
                    getattr(task, "id", "?"),
                    exc_info=True,
                )

        self._log.info(
            "[task:%s] Model change recorded: %s → %s (agent=%s, reason=%s, policy=%s)",
            getattr(task, "id", "?"),
            authoritative, dispatch_model, agent_name, reason, policy,
        )
        return True

    # ------------------------------------------------------------------
    # _build_clarification_prompt() — Gate Stage
    # ------------------------------------------------------------------

    def _build_clarification_prompt(
        self,
        spec: str,
        clarification_path: str,
        preview_path: str,
        quick: bool = False,
    ) -> str:
        """Build the prompt for the clarification gate stage.

        Asks the LLM to read the spec and surface genuine ambiguities
        before any task breakdown is written.  The agent writes a
        clarification JSON (questions + summary) and an HTML preview
        page to the specified paths.
        """
        quick_instruction = ""
        if quick:
            quick_instruction = (
                "\n\nDo NOT explore or read the codebase. Base your analysis "
                "solely on the spec text below."
            )

        return f"""You are a spec analyst. Read the specification below and identify \
what you are NOT sure about.{quick_instruction}

Your job is to surface ambiguities, unstated assumptions, and decisions that \
could go wrong — BEFORE any task breakdown is written. A wrong assumption now \
costs hours of rework later.

Write TWO files:

1. A clarification JSON to `{clarification_path}` with this exact structure:

```json
{{
  "questions": [
    "Specific question about something ambiguous in the spec",
    "Another genuine question about a decision that needs clarifying"
  ],
  "summary": "A short plain-words description of what you think this project \
builds. 2-3 sentences, no jargon."
}}
```

Rules for questions:
- Ask up to 5 questions. Only ask about things you genuinely cannot determine \
from the spec.
- Each question must be specific to THIS spec — no generic filler.
- Focus on decisions where getting it wrong would be expensive: data semantics, \
scope boundaries, tech choices the spec leaves open.
- If the spec is completely clear, write an empty questions list and say so in \
the summary.

2. An HTML preview to `{preview_path}` — a single standalone HTML page showing \
what the FINISHED result will look like when done. This is a visual mockup of \
the end product so the human can confirm you understood correctly. Use inline \
CSS, realistic placeholder data, and the structure the finished product would have.

Specification:
---
{spec}
---

Write both files now."""

    # ------------------------------------------------------------------
    # _build_plan_prompt() — Unified Prompt Builder
    # ------------------------------------------------------------------

    def _build_plan_prompt(
        self,
        spec: str,
        plan_path: str,
        available_agents: List[Dict[str, Any]],
        quota: Optional[Dict[str, Dict[str, float]]] = None,
        quick: bool = False,
    ) -> str:
        """Build the unified plan prompt used by all modes.

        Single function for interactive, auto, and quiet.  Includes agent
        context, quota data, schema, dependency/artifact rules, and the
        instruction to write the plan JSON to ``plan_path``.

        Does NOT tell the agent to output JSON in the terminal.
        """
        quota_instruction = ""
        if quota:
            quota_instruction = f"""
- "usage_pct" and "remaining_pct" show current quota utilization. Avoid assigning to agents with high usage (>{self.config.quota_threshold}%) when alternatives exist."""

        # Routing guidance: one shared source for planner and operator
        # assignments (docs/routing_guidance.md). Small on purpose; injected
        # verbatim so lane rules (e.g. agy owns browser/screenshot proof)
        # reach every plan.
        guidance_note = ""
        try:
            from pathlib import Path as _P
            _gp = _P(self.config.project_root if getattr(self.config, "project_root", None) else ".") / "docs" / "routing_guidance.md"
            if _gp.is_file():
                guidance_note = "\n\nROUTING GUIDANCE (follow the lane rules; name the rule you applied per assignment):\n" + _gp.read_text()
        except Exception:
            guidance_note = ""

        # Brief template: how task descriptions must read (plain voice, fixed
        # shape). Same file the operator's loaders follow — one standard.
        try:
            _tp = _P(self.config.project_root if getattr(self.config, "project_root", None) else ".") / "docs" / "task_brief_template.md"
            if _tp.is_file():
                guidance_note += "\n\nBRIEF TEMPLATE (every task description follows this shape and voice, exactly):\n" + _tp.read_text()
        except Exception:
            pass

        quick_instruction = ""
        if quick:
            quick_instruction = """

QUICK MODE: Do NOT explore or read the codebase. Do NOT use tools to inspect files, directories, or project structure. Generate the task plan directly and solely from the specification provided below. Work only with the information given in the spec."""

        return f"""You are a task planner. Break the specification below into sub-tasks for AI agents.{quick_instruction}{guidance_note}

PLANNING PHILOSOPHY:
- Plan at the level a competent developer would delegate: clear intent, key constraints, and gotchas — not step-by-step instructions.
- Each task description should give the executing agent enough context to start working independently, but trust it to figure out implementation details.
- Prefer fewer, meaningful tasks over many granular ones. If two things are naturally done together, keep them as one task.
- Only mention gotchas, constraints, or coordination points that the agent wouldn't discover on its own.

PROOF-FIRST DECOMPOSITION:
Structure the DAG so every task produces an observable, verifiable result — not just compilable code.
- Prefer VERTICAL SLICES over horizontal layers. Each task should deliver a working feature end-to-end (data + logic + UI + wiring) rather than one technical layer across many features. A task that builds invisible infrastructure with no way to verify it visually is a task that can silently fail.
- For UI / mobile / frontend specs: Task 1 should create a running app shell with navigation and at least one visible screen. Every subsequent task should add a complete screen or flow that a user (or reviewer) can navigate to and verify. Never defer all navigation/integration wiring to a final task.
- For backend / API specs: each task should produce a callable endpoint or a runnable script that demonstrates the behavior — not just models or utilities that only become testable after a later integration task.
- Ask yourself for each task: "How will the reviewer verify this worked?" If the answer is "they can't until a later task integrates it," restructure so that each task is independently verifiable.
- The description for each task MUST include a "Proof" line stating what the executing agent should demonstrate (e.g., "Proof: screenshot of Home screen with navigation buttons", "Proof: curl command showing API response").

Available agents:
{json.dumps(available_agents, indent=2)}

Each agent entry shows:
- "capabilities": what the agent can do
- "cost_tier": low/medium/high cost
- "models": available AI models with descriptions and enabled state{quota_instruction}

AGENT DISTRIBUTION:
- Odin's router auto-selects agents from the cheapest viable cost tier.
- Within a tier, it distributes randomly — do NOT assign all tasks to one agent.
- Distribute your suggested_agent across agents at the same cost tier when they are equally capable.
- Use higher-tier agents only when the task genuinely requires their unique capabilities.

Task specification:
---
{spec}
---

For each sub-task, decide which agent is best, considering capabilities, quota, cost, and task needs. Your plan must be a JSON array where each element has:
- "id": symbolic identifier like "task_1", "task_2", etc.
- "title": short title
- "description": what to do, why it matters, and any non-obvious constraints. Write this as a brief for a capable agent — not a tutorial.
- "required_capabilities": list of capabilities needed
- "suggested_agent": which agent should handle this
- "suggested_model": (optional) specific model to use if the task needs a particular model's strengths
- "complexity": "low" (mechanical), "medium" (standard), or "high" (complex reasoning)
- "depends_on": list of task IDs this must wait for ([] if none)
- "expected_outputs": list of artifacts produced. Be specific about filenames only when parallel tasks must coordinate on shared files.

DEPENDENCY RULES:
- Independent tasks should have depends_on: []
- Tasks that read another task's output MUST depend on it
- When in doubt, add the dependency — correctness over parallelism

ARTIFACT COORDINATION:
- Parallel tasks feeding into a merge task MUST agree on filenames upfront.
- Sequential tasks can determine filenames as they go.

Write your final plan as a JSON array to: `{plan_path}`

After writing the plan file, tell the user: "Planning complete. Press Stop or Ctrl+C to finish and create tasks."
Do not take any further actions after writing the plan."""

    # ------------------------------------------------------------------
    # _create_tasks_from_plan()
    # ------------------------------------------------------------------

    async def _create_tasks_from_plan(
        self,
        sub_tasks: List[Dict[str, Any]],
        spec_id: str,
        quota: Optional[Dict[str, Dict[str, float]]] = None,
        routing_config: Optional[Dict[str, Any]] = None,
        skip_reflection: bool = False,
    ) -> List[Task]:
        """Two-pass task creation: create tasks, then resolve dependencies.

        Pass 1: Create tasks and build symbolic→real ID map.
        Pass 2: Resolve depends_on from symbolic IDs to real UUIDs.

        Used by plan() for all modes (interactive, auto, quiet).
        """
        symbolic_to_real: Dict[str, str] = {}
        tasks: List[Task] = []
        task_ids: List[str] = []
        for st in sub_tasks:
            complexity = st.get("complexity", "medium")
            agent_name, selected_model, routing_reasoning, assignment_reason = await self._route_task(
                st.get("required_capabilities", []),
                complexity,
                st.get("suggested_agent"),
                quota,
                routing_config=routing_config,
                suggested_model=st.get("suggested_model"),
            )

            task_metadata = {
                **st.get("metadata", {}),
                "required_capabilities": st.get("required_capabilities", []),
                "suggested_agent": st.get("suggested_agent"),
                "suggested_model": st.get("suggested_model"),
                "complexity": complexity,
                "routing_reasoning": routing_reasoning,
                "assignment_reason": assignment_reason,
            }
            if st.get("expected_outputs"):
                task_metadata["expected_outputs"] = st["expected_outputs"]
            if st.get("assumptions"):
                task_metadata["assumptions"] = st["assumptions"]
            if selected_model:
                task_metadata["selected_model"] = selected_model
            if st.get("reasoning"):
                task_metadata["reasoning"] = st["reasoning"]
            if quota:
                agent_name_for_quota = st.get("suggested_agent")
                if agent_name_for_quota and agent_name_for_quota in quota:
                    task_metadata["quota_snapshot"] = quota[agent_name_for_quota]
            task = self.task_mgr.create_task(
                title=st["title"],
                description=st["description"],
                metadata=task_metadata,
                spec_id=spec_id,
                skip_reflection=skip_reflection,
            )
            # Map symbolic ID (e.g. "task_1") to real UUID
            symbolic_id = st.get("id", "")
            if symbolic_id:
                symbolic_to_real[symbolic_id] = task.id

            self.task_mgr.assign_task(task.id, agent_name)

            # Post planning assumptions as initial comment for visibility
            if st.get("assumptions"):
                assumptions_text = "Planning assumptions:\n" + "\n".join(
                    f"- {a}" for a in st["assumptions"]
                )
                self.task_mgr.add_comment(
                    task_id=task.id,
                    author="odin",
                    content=assumptions_text,
                )

            task = self.task_mgr.get_task(task.id)
            tasks.append(task)
            task_ids.append(task.id)
            self.logger.log(
                action="task_assigned",
                task_id=task.id,
                agent=agent_name,
                metadata={"title": st["title"]},
            )

        # Pass 2: Resolve depends_on from symbolic IDs to real UUIDs
        for i, st in enumerate(sub_tasks):
            symbolic_deps = st.get("depends_on", [])
            if not symbolic_deps:
                continue
            real_deps = []
            for dep in symbolic_deps:
                real_id = symbolic_to_real.get(dep)
                if real_id:
                    real_deps.append(real_id)
                else:
                    self.logger.log(
                        action="dep_warning",
                        metadata={"symbolic_dep": dep, "task": tasks[i].id},
                    )
                    self.task_mgr.add_comment(
                        task_id=tasks[i].id,
                        author="odin",
                        content=f"Dependency '{dep}' could not be resolved and was dropped.",
                    )
            if real_deps:
                task = self.task_mgr.get_task(tasks[i].id)
                task.depends_on = real_deps
                self.task_mgr.update_task(task)
                tasks[i] = task

        return tasks

    # ------------------------------------------------------------------
    # _clean_interactive_transcript()
    # ------------------------------------------------------------------

    # Spinner/indicator characters emitted by CLI TUIs (Claude Code, etc.)
    _SPINNER_CHARS = r"✦✳✶✻✢·⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏●◐◓◑◒⠐⠂\*"

    # Patterns that identify TUI noise lines (compiled once at class level).
    # Must handle both tmux scrollback and raw `script` byte-stream output
    # (where cursor-positioning creates fragmented text).
    _TUI_NOISE_RE = re.compile(
        r"^("
        # ---- Spinner / progress ----
        # Spinner char(s) alone or with short fragment (TUI redraw artifacts)
        rf"[{_SPINNER_CHARS}]+(\s*\S{{0,4}})?$"
        # Spinner char + word ending in ellipsis
        rf"|[{_SPINNER_CHARS}]\s*\S+[.…]{{1,3}}"
        # Spinner status text (Recombobulating, Moseying, etc.)
        r"|.*(?:Recombobulating|Moseying|Cogitating|Perambulating|Ruminating|Pontificating|Sauntering)\S*[.…]*.*"
        # (thinking) / (thought for Xs) — standalone or with any prefix
        r"|.*\(thinking\)"
        r"|.*\(thought for \d+s?\)"
        # [Request interrupted by user] and similar system messages
        r"|\[Request interrupted.*\]"
        # ---- OSC / terminal control remnants ----
        # Terminal title, progress: "0;✳ Claude Code", "9;4;0;", etc.
        r"|\d+;[\d;]*.*"
        # Hyperlink OSC remnants: "8;;file:///..."
        r"|8;;.*"
        # ---- TUI chrome (with and without spaces) ----
        r"|Collapse"
        r"|No\s*recent\s*activity|Norecentactivity|Recentactivity"
        r"|esc\s*to\s*interrupt|esctointerrupt"
        r"|\?\s*for\s*shortcuts|\?forshortcuts"
        r"|Update\s*available.*|Updateavailable.*"
        r"|ctrl\+\w\s*to\s*\w+.*|ctrl\+\w+to\w+.*"
        r"|/ide\s*for\s*\w+.*|/idefor\w+.*"
        r"|Press\s*Ctrl-C.*|PressCtrl-C.*"
        r"|Resume\s+this\s+session.*"
        r"|claude\s+--resume\s+\S+"
        r"|…\+\d+lines.*"
        # ---- Model / status badges ----
        r"|(Opus|Sonnet|Haiku)[\d.]+[·.].*"
        r"|\S+·Claude\s*(Max|Pro|Free)"
        # ---- TUI welcome (spaces collapsed by cursor-positioning) ----
        r"|ClaudeCodev[\d.]+"
        r"|Tipsforgettingstarted"
        r"|Welcomeback\S*"
        # ---- Bare prompt with no user input ----
        r"|❯\s*$"
        # ---- Standalone path (CWD display in prompt) ----
        r"|~/\S+$"
        r")$",
        re.IGNORECASE,
    )

    # OSC sequences that survive basic ANSI stripping.
    # Matches \x1b] ... (terminated by BEL \x07 or ST \x1b\\)
    _OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")

    # Substrings that identify TUI chrome even when concatenated on one line.
    # Used with re.search (not re.match) to catch "esctointerruptUpdateavailable..."
    _TUI_CHROME_RE = re.compile(
        r"esctointerrupt|esc to interrupt"
        r"|\?forshortcuts|\? for shortcuts"
        r"|Updateavailable|Update available"
        r"|ctrl\+\w+to\w+|ctrl\+\w+ to \w+"
        r"|/idefor|/ide for"
        r"|brewupgrade|brew upgrade"
        r"|PressCtrl-C|Press Ctrl-C"
        r"|StoporCtrl|Stop or Ctrl"
        r"|tofinishandcreatetasks|to finish and create tasks",
        re.IGNORECASE,
    )

    @staticmethod
    def _clean_interactive_transcript(text: str) -> str:
        """Clean TUI artifacts from interactive transcript.

        Handles both tmux scrollback and raw ``script`` output.  The raw
        byte-stream from ``script`` contains cursor-positioning, spinner
        animation frames, OSC sequences, and TUI chrome that must be
        stripped to produce a human-readable planning trace.
        """
        from odin.logging.logger_utils import strip_ansi

        # 1. Strip OSC sequences FIRST (not covered by the CSI-only ANSI regex)
        cleaned = Orchestrator._OSC_RE.sub("", text)
        # 2. Belt-and-suspenders CSI / SGR removal
        cleaned = strip_ansi(cleaned)
        # 3. Box-drawing (U+2500–U+257F) and block elements (U+2580–U+259F)
        cleaned = re.sub(r"[\u2500-\u257F\u2580-\u259F]", "", cleaned)
        # 4. Control characters (keep \n \t \r for structure)
        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)
        # 5. Strip inline hyperlink OSC remnants (8;;url8;;)
        cleaned = re.sub(r"8;;[^\s\n]*", "", cleaned)
        # 6. Strip trailing whitespace per line
        cleaned = re.sub(r"[ \t]+$", "", cleaned, flags=re.MULTILINE)

        # Line-level filtering
        out: list[str] = []
        prev = None
        for line in cleaned.split("\n"):
            stripped = line.strip()
            # Drop known TUI noise patterns
            if stripped and Orchestrator._TUI_NOISE_RE.match(stripped):
                continue
            # Drop short alphabetic fragments (spinner redraw artifacts
            # like "R", "Ro", "em", "boul", "ecmb").  Preserves code
            # tokens like "}", "]", "{", digits, and indented content.
            if 0 < len(stripped) <= 5 and stripped.isalpha():
                continue
            # Drop standalone orphaned symbols (e.g. "…" from spinner)
            if stripped in {"…", "·", "–", "—", "•"}:
                continue
            # Drop bare ⏺ markers and gutted tool calls (e.g. "⏺Write("
            # left after hyperlink stripping)
            if stripped == "⏺" or re.match(r"^⏺\w*\(\s*\)?$", stripped):
                continue
            # Drop lines containing TUI chrome substrings (catches
            # concatenated chrome like "esctointerruptUpdateavailable...")
            # but preserve agent output lines (⏺, ⎿).
            if (stripped
                    and not stripped.startswith(("⏺", "⎿"))
                    and Orchestrator._TUI_CHROME_RE.search(stripped)):
                continue
            # Drop lines with very low space density — collapsed cursor-
            # positioning artifacts (e.g. "Ineedhelpplanningthistask...").
            # Preserves agent output (⏺/⎿), user input (❯), and
            # indented content (code/JSON).
            if (len(stripped) > 15
                    and not stripped.startswith(("⏺", "⎿", "❯"))
                    and not line.startswith((" ", "\t"))
                    and stripped.count(" ") / len(stripped) < 0.05):
                continue
            # Deduplicate consecutive identical lines
            if stripped == prev:
                continue
            out.append(line)
            prev = stripped

        cleaned = "\n".join(out)
        # Collapse 3+ consecutive blank lines to 2
        cleaned = re.sub(r"(\n\s*){3,}", "\n\n", cleaned)

        # Deduplicate repeated paragraph blocks (caused by session
        # restarts after [Request interrupted by user]).
        paragraphs = cleaned.split("\n\n")
        seen: set[str] = set()
        deduped: list[str] = []
        for para in paragraphs:
            key = para.strip()
            if key and key in seen:
                continue
            seen.add(key)
            deduped.append(para)
        cleaned = "\n\n".join(deduped)

        return cleaned.strip()

    # ------------------------------------------------------------------
    # _run_interactive_plan()
    # ------------------------------------------------------------------

    def _run_interactive_plan(
        self,
        prompt: str,
        working_dir: str,
        quick: bool = False,
        direct: bool = False,
    ) -> Optional[str]:
        """Launch interactive session for planning.

        The agent receives the unified plan prompt (same as auto/quiet) and
        writes its plan JSON to the plan_path specified in the prompt.
        Blocks until the user exits the session.

        When *direct* is True, runs the agent CLI as a direct subprocess
        without tmux wrapping — for web UI / PTY contexts where the calling
        process already provides the terminal.

        Returns the path to the transcript log (for trace capture), or None.
        """
        base_name, model = self._planning_agent_model()
        base_cfg = self.config.agents.get(base_name)
        if not base_cfg:
            raise RuntimeError(f"Planning agent '{base_name}' not found in config")

        self._assert_agent_cli_available(base_name, action="planning")

        harness = get_harness(base_name, base_cfg)
        context = {"working_dir": working_dir}
        if model:
            context["model"] = model

        session = InteractivePlanSession(
            harness=harness,
            system_prompt=prompt,
            context=context,
            log_dir=self.config.log_dir,
            direct=direct,
        )
        # run() is synchronous — blocks while user interacts with the agent
        return session.run()

    # ------------------------------------------------------------------
    # _download_reference_images()
    # ------------------------------------------------------------------

    def _download_reference_images(self, task_id: str, working_dir: str) -> list:
        """Download task reference images to working_dir/.odin/task_images/{task_id[:8]}/.

        Returns list of local file paths. Silently skips images that fail to download.
        """
        task_data = self.task_mgr.get_task_raw(task_id)
        images = task_data.get("reference_images", [])
        if not images:
            return []

        img_dir = Path(working_dir) / ".odin" / "task_images" / task_id[:8]
        img_dir.mkdir(parents=True, exist_ok=True)

        paths = []
        for img in images:
            url = img.get("url", "")
            filename = img.get("original_filename", f"image_{img.get('id', 'unknown')}.png")
            if not url:
                continue
            try:
                resp = httpx.get(url, follow_redirects=True, timeout=30)
                resp.raise_for_status()
                dest = img_dir / filename
                dest.write_bytes(resp.content)
                paths.append(str(dest))
                self._log.info("[task:%s] Downloaded reference image: %s", task_id, filename)
            except Exception:
                self._log.warning(
                    "[task:%s] Failed to download reference image: %s", task_id, url, exc_info=True
                )
        return paths

    # ------------------------------------------------------------------
    # exec_task()
    # ------------------------------------------------------------------

    async def exec_task(
        self, task_id: str, working_dir: Optional[str] = None, mock: bool = False
    ) -> Dict[str, Any]:
        """Execute a single task by ID.

        Reads the assigned agent and description from the task, executes it,
        and updates the task status.  Checks dependencies first — if any
        dependency has failed, the task is marked FAILED without executing.

        Args:
            mock: If True, skip all backend writes (status changes, comments,
                cost tracking). The harness still runs and results are returned,
                but nothing is persisted. Used for local-only testing.
        """
        # Executor cold-start backstop: on the first real (non-mock)
        # execution in this process, sweep any ``odin-msb-*`` sandboxes a
        # crashed previous process left behind (their per-run finally never
        # ran). Once-per-process so it cannot race this process's own runs.
        if not mock:
            self._maybe_sweep_orphaned_sandboxes()

        # Resolve prefix
        try:
            full_id = self.task_mgr.resolve_task_id(task_id) or task_id
        except BackendUnreachable:
            full_id = task_id
        task = self.task_mgr.get_task(full_id)
        if not task:
            raise RuntimeError(f"Task not found: {task_id}")
        if not task.assigned_agent:
            raise RuntimeError(
                f"Task {full_id} has no assigned agent. Use 'odin assign' first."
            )

        # Check dependencies before executing — skip without changing status
        if task.depends_on:
            dep_status = check_deps(task, self._task_resolver)

            if dep_status == DepStatus.BLOCKED:
                failed = get_failed_deps(task, self._task_resolver)
                failed_ids = ", ".join(d[:8] for d in failed)
                # Build human-readable list of failed dep titles
                failed_titles = []
                for fid in failed:
                    ft = self._task_resolver(fid)
                    failed_titles.append(f"- {fid[:8]}: {ft.title}" if ft else f"- {fid[:8]}")
                reason = f"Skipped — dependency failed: {failed_ids}"
                self._log.warning(
                    "[task:%s] Blocked — failed deps: %s", full_id, failed_ids,
                )
                self.task_mgr.add_comment(
                    task_id=full_id,
                    author="odin",
                    content=f"Blocked — upstream dependencies failed:\n" + "\n".join(failed_titles),
                )
                self.logger.log(
                    action="task_blocked",
                    task_id=full_id,
                    metadata={"failed_deps": failed, "reason": reason},
                )
                return {
                    "task_id": full_id,
                    "success": False,
                    "output": "",
                    "error": reason,
                }

            if dep_status == DepStatus.WAITING:
                unmet = get_unmet_deps(task, self._task_resolver)
                unmet_ids = ", ".join(d[:8] for d in unmet)
                unmet_titles = []
                for uid in unmet:
                    ut = self._task_resolver(uid)
                    unmet_titles.append(f"- {uid[:8]}: {ut.title}" if ut else f"- {uid[:8]}")
                reason = f"Skipped — dependencies not yet completed: {unmet_ids}"
                self._log.info(
                    "[task:%s] Waiting — unmet deps: %s", full_id, unmet_ids,
                )
                self.task_mgr.add_comment(
                    task_id=full_id,
                    author="odin",
                    content=f"Waiting — dependencies not yet completed:\n" + "\n".join(unmet_titles),
                )
                self.logger.log(
                    action="task_blocked",
                    task_id=full_id,
                    metadata={"unmet_deps": unmet, "reason": reason},
                )
                return {
                    "task_id": full_id,
                    "success": False,
                    "output": "",
                    "error": reason,
                }

        # Resolve working dir: from spec metadata, or cwd
        if not working_dir:
            if task.spec_id:
                spec = self.spec_store.load(task.spec_id)
                if spec and spec.metadata.get("working_dir"):
                    working_dir = spec.metadata["working_dir"]
            if not working_dir:
                working_dir = str(Path.cwd())

        # Inject upstream context from completed dependencies.
        # exec_all() does this with in-memory completed_outputs; here we fetch
        # from comments (persisted by previous executions).
        desc = task.description or ""
        if task.depends_on:
            upstream_parts = []
            for dep_id in task.depends_on:
                dep_task = self.task_mgr.get_task(dep_id)
                if dep_task and dep_task.status in (
                    TaskStatus.DONE, TaskStatus.REVIEW
                ):
                    comments = self.task_mgr.get_comments(dep_id)
                    if comments:
                        # Use latest comment (contains ODIN-SUMMARY from execution)
                        latest = comments[-1]
                        content = latest.get("content", "") if isinstance(latest, dict) else getattr(latest, "content", "")
                        if content:
                            upstream_parts.append(
                                f"Context from upstream task {dep_id[:8]} ({dep_task.title}):\n{content[:2000]}"
                            )
            if upstream_parts:
                context_block = "\n\n".join(upstream_parts)
                desc = f"{context_block}\n\n---\n\n{desc}"

        # Inject comprehensive task context (reflections, summary, human
        # notes, Q&A, proof, agent output) — replaces the narrower
        # _build_reflection_context + _build_self_context pair.
        task_ctx = self._build_task_context(full_id)
        if task_ctx:
            desc = f"{task_ctx}\n\n---\n\n{desc}"

        # Reuse an existing task worktree on retries. The previous logic saw
        # branch/worktree metadata and skipped creating a nested worktree, but
        # it did not switch working_dir back to that worktree, so retries ran
        # in the project root.
        existing_worktree_path = task.metadata.get("worktree_path") if task.metadata else None
        if existing_worktree_path and Path(existing_worktree_path).exists():
            working_dir = str(Path(existing_worktree_path).resolve())
            self._log.info("[task:%s] Reusing existing worktree: %s", full_id, working_dir)

        # Download reference images and inject file paths into prompt
        image_paths = self._download_reference_images(full_id, working_dir)
        if image_paths:
            relative_paths = [os.path.relpath(p, working_dir) for p in image_paths]
            image_block = "## Reference Images\n\n"
            image_block += "The following reference images were provided with this task. "
            image_block += "Read them to understand the visual requirements:\n\n"
            for rp in relative_paths:
                image_block += f"- {rp}\n"
            desc = f"{image_block}\n---\n\n{desc}"

        # Create task worktree if enabled
        # Skip if the DAG executor already created the worktree (worktree_path
        # or branch already present in metadata — avoids nested worktree inside
        # the cwd that the DAG executor set to the worktree path).
        worktree_path = None
        dag_already_created = bool(existing_worktree_path and Path(existing_worktree_path).exists())
        if task.spec_id and not mock and not dag_already_created and (self._worktree or self.config.worktree_enabled):
            # Try auto-init if worktree manager isn't ready yet
            if not self._worktree:
                self._ensure_git_repo()

            if self._worktree:
                try:
                    spec_branch = self._worktree.create_spec_branch(
                        task.spec_id, base_branch=self.config.base_branch,
                    )
                    # Ensure the spec metadata has the branch key (may be
                    # missing for cloned specs whose plan step was skipped).
                    # Try local store first, fall back to backend API for
                    # specs that only exist in taskit (e.g. clones).
                    spec_obj = self.spec_store.load(task.spec_id)
                    if not spec_obj and self._spec_backend:
                        spec_obj = self._spec_backend.load_spec(task.spec_id)
                        if spec_obj:
                            self.spec_store.save(spec_obj)
                            self._log.info("[task:%s] Synced spec %s from backend to local store", full_id, task.spec_id)
                    if spec_obj and not spec_obj.metadata.get("branch"):
                        self._update_spec_metadata(task.spec_id, {"branch": spec_branch})
                        self._log.info("[task:%s] Backfilled spec branch metadata: %s", full_id, spec_branch)
                    worktree_path = self._worktree.create_task_worktree(
                        spec_id=task.spec_id,
                        task_id=full_id,
                        post_hooks=self.config.worktree_post_hooks,
                        symlinks=self.config.worktree_symlinks,
                    )
                    working_dir = str(worktree_path)
                    task_branch = f"task/{task.spec_id}/{full_id}"
                    task.metadata["branch"] = task_branch
                    task.metadata["worktree_path"] = str(worktree_path)
                    self.task_mgr.update_task(task)
                    self._log.info("[task:%s] Using worktree: %s", full_id, worktree_path)
                    self.task_mgr.add_comment(
                        task_id=full_id,
                        author="odin",
                        content=f"Git isolation active — working in branch `{task_branch}`",
                        comment_type="status_update",
                    )
                except Exception as exc:
                    # Never fall back to running in the project root: a
                    # non-isolated agent edits and commits on the operator's
                    # branch (proven twice: F44 spec-less tasks, F49/F51
                    # parallel-dispatch worktree race). Fail loudly; a
                    # redispatch retries worktree creation cleanly.
                    self._log.error(
                        "[task:%s] Failed to create worktree — refusing to run "
                        "without isolation", full_id, exc_info=True,
                    )
                    task = self.task_mgr.get_task(full_id) or task
                    task.metadata["worktree_status"] = "failed"
                    task.metadata["worktree_error"] = str(exc)
                    self.task_mgr.update_task(task)
                    self.task_mgr.add_comment(
                        task_id=full_id,
                        author="odin",
                        content=(
                            f"Worktree creation failed: {exc}\n\n"
                            "Refusing to execute without git isolation "
                            "(project-root execution is banned — F44/F51). "
                            "Fix the cause and redispatch."
                        ),
                        comment_type="status_update",
                    )
                    raise WorktreeIsolationError(
                        f"Worktree creation failed for task {full_id}: {exc}"
                    ) from exc
            else:
                reason = self._worktree_disabled_reason or "unknown"
                self.task_mgr.add_comment(
                    task_id=full_id,
                    author="odin",
                    content=(
                        f"Git isolation unavailable: {reason}\n\n"
                        "Task will run without git isolation. "
                        "Run `odin init` in the project directory to enable worktree isolation."
                    ),
                    comment_type="status_update",
                )

        sem = asyncio.Semaphore(1)

        # Warm start: match this task's title/description against the repo's
        # breadcrumb + pattern docs and surface up to 3 paths the agent should
        # read first. Same dependency-free TF-IDF matching the Memory twins
        # service uses (odin.warm_start mirrors taskit-backend/tasks/similarity).
        # Paths only — near-zero token cost; no section when nothing matches.
        # Logged to task.metadata["warm_start_docs"] so the wave-6 audit can
        # measure whether agents actually read what was suggested.
        warm_section = ""
        warm_suggestions: List[dict] = []
        try:
            warm_suggestions = suggest_warm_start_docs(
                task.title or "", task.description or "", Path(working_dir) / "docs",
            )
            warm_section = format_warm_start_section(warm_suggestions)
        except Exception:
            self._log.debug("[task:%s] warm-start suggestion failed", full_id, exc_info=True)
        if warm_section:
            desc = warm_section + desc
        self._record_warm_start_docs(full_id, warm_suggestions, mock=mock)

        # Task #332: if the prior attempt truncated at the output cap with
        # work in the worktree, this dispatch is a RESUME. Rebuild the resume
        # prompt host-side from durable pieces only — the brief (already in
        # desc), a git status/diff summary of THIS worktree, and the tail of
        # the previous attempt's trace — plus the standing "verify the working
        # state first" instruction. No provider-session state is read, so any
        # agent can continue a run another agent started.
        if (task.metadata or {}).get("truncation_resume_pending"):
            resume_block = self._build_resume_prompt(full_id, working_dir, task)
            if resume_block:
                desc = resume_block + "\n\n---\n\n" + desc
                self._log.info(
                    "[task:%s] Resuming truncated attempt in worktree %s",
                    full_id, working_dir,
                )

        try:
            result = await self._execute_task(
                full_id, task.assigned_agent, desc, working_dir, sem, mock=mock
            )
        except Exception:
            self._log.warning(
                "[task:%s] Execution raised before terminal status persisted; leaving task state to the task executor",
                full_id,
            )
            raise

        # Auto-commit and defer merge to reflection pass
        # Merge happens in views.py:_merge_task_on_reflection_pass() when
        # reflection approves the work (REVIEW → TESTING). This ensures
        # reflection-driven re-executions are captured before merging.
        if worktree_path and task.spec_id:
            task_branch = f"task/{task.spec_id}/{full_id}"
            try:
                if result.get("success"):
                    # Auto-commit any uncommitted work so it's on the task branch
                    ac = self._worktree._auto_commit_worktree(task.spec_id, full_id, task.title or "")
                    task = self.task_mgr.get_task(full_id) or task
                    task.metadata["merge_status"] = "deferred"
                    self.task_mgr.update_task(task)
                    # Surface defence-in-depth exclusions (venv/bulk-guard)
                    # in the task comment so the operator never misses a
                    # refused pollution sweep.
                    content = (
                        f"Work committed on branch `{task_branch}` — "
                        "merge deferred until reflection passes"
                    )
                    if getattr(ac, "skipped", 0):
                        content += f"\n\n{ac.summary}"
                    self.task_mgr.add_comment(
                        task_id=full_id,
                        author="odin",
                        content=content,
                        comment_type="status_update",
                    )
                else:
                    task = self.task_mgr.get_task(full_id) or task
                    task.metadata["merge_status"] = "pending"
                    self.task_mgr.update_task(task)
                    self.task_mgr.add_comment(
                        task_id=full_id,
                        author="odin",
                        content=f"Task failed — branch `{task_branch}` preserved (not merged)",
                        comment_type="status_update",
                    )
            except Exception as exc:
                self._log.warning(
                    "[task:%s] Post-execution auto-commit failed", full_id, exc_info=True,
                )
                self.task_mgr.add_comment(
                    task_id=full_id,
                    author="odin",
                    content=f"Post-execution auto-commit error: {exc}",
                    comment_type="status_update",
                )

            # Worktree is preserved after merge so the user can open it
            # in their editor to inspect the work. Cleanup happens at
            # spec finalization (finalize_spec) or manually.

        return result

    # ------------------------------------------------------------------
    # run() — convenience plan-only
    # ------------------------------------------------------------------

    async def run(self, spec: str, working_dir: Optional[str] = None) -> Tuple[str, List[Task]]:
        """Plan tasks from a spec string.

        Convenience method that calls plan().  Execution is handled
        separately — either by ``odin exec <task_id>`` one task at a time,
        or by the Celery DAG executor when using the TaskIt backend.

        Returns ``(spec_id, tasks)`` so the caller can decide what to do next.
        """
        wd = working_dir or str(Path.cwd())
        self.logger.log(action="run_started", metadata={"spec_length": len(spec)})

        sid, tasks = await self.plan(spec, wd)

        self.logger.log(action="run_completed", metadata={"task_count": len(tasks)})
        return sid, tasks

    # ------------------------------------------------------------------
    # Reflection feedback injection
    # ------------------------------------------------------------------

    # DEPRECATED — replaced by _build_task_context
    def _build_reflection_context(self, task_id: str) -> str:
        """Build a context block from the task's latest reflection comment.

        .. deprecated:: Replaced by :meth:`_build_task_context` which
           collects all comment types in a single pass.

        Scans comments in reverse for the most recent reflection.  If found
        (and it has a NEEDS_WORK verdict), returns a formatted block that
        tells the agent what to fix.  Returns empty string otherwise.
        """
        try:
            comments = self.task_mgr.get_comments(task_id)
        except Exception:
            return ""

        if not comments:
            return ""

        # Find the latest reflection comment (reverse scan)
        for i in range(len(comments) - 1, -1, -1):
            c = comments[i]
            if _comment_attr(c, "comment_type") != "reflection":
                continue

            content = _comment_attr(c, "content", "")
            if not content:
                continue

            # Only inject for NEEDS_WORK verdicts (the ones that trigger retry)
            attachments = _comment_attr(c, "attachments", [])
            verdict = None
            for att in (attachments or []):
                if isinstance(att, dict) and att.get("type") == "reflection":
                    verdict = (att.get("verdict") or "").upper()
                    break

            if verdict != "NEEDS_WORK":
                continue

            reviewer = (
                _comment_attr(c, "author_label")
                or _comment_attr(c, "author_email", "reviewer")
            )
            lines = [
                "⚠️ PREVIOUS ATTEMPT REVIEWED — NEEDS WORK",
                f"Reviewer: {reviewer}",
                "",
                "Address ALL of the following issues before resubmitting:",
                "",
                content,
            ]
            return "\n".join(lines)

        return ""

    # ------------------------------------------------------------------
    # Self-context injection (summary checkpoint)
    # ------------------------------------------------------------------

    # DEPRECATED — replaced by _build_task_context
    def _build_self_context(self, task_id: str) -> str:
        """Build a context block from the task's latest summary comment.

        .. deprecated:: Replaced by :meth:`_build_task_context` which
           collects all comment types in a single pass.

        If a summary comment exists, returns a string of the form:
            Task summary (from <author_label>):
            <summary content>

            Human notes added since summary:
            - [<author_label>]: <content>

        Returns an empty string if no summary comment is found (no-op).
        """
        try:
            comments = self.task_mgr.get_comments(task_id)
        except Exception:
            return ""

        if not comments:
            return ""

        # Find the latest summary comment by scanning in reverse order
        latest_summary_idx = None
        for i in range(len(comments) - 1, -1, -1):
            c = comments[i]
            if _comment_attr(c, "comment_type") == "summary":
                latest_summary_idx = i
                break

        if latest_summary_idx is None:
            return ""

        summary_comment = comments[latest_summary_idx]
        summary_content = _comment_attr(summary_comment, "content", "")
        summary_label = (
            _comment_attr(summary_comment, "author_label")
            or _comment_attr(summary_comment, "author_email", "AI Summary")
        )

        # Collect human notes after the summary (exclude agents and system)
        human_notes = []
        for c in comments[latest_summary_idx + 1:]:
            email = _comment_attr(c, "author_email", "")
            ctype = _comment_attr(c, "comment_type")
            # Skip agent comments, system comments, and nested summaries
            if email.endswith("@odin.agent") or email == "system@taskit" or ctype == "summary":
                continue
            label = _comment_attr(c, "author_label") or email
            content = _comment_attr(c, "content", "")
            if content:
                human_notes.append(f"- [{label}]: {content}")

        lines = [f"Task summary (from {summary_label}):", summary_content]
        if human_notes:
            lines.append("")
            lines.append("Human notes added since summary:")
            lines.extend(human_notes)

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Comprehensive task context injection
    # ------------------------------------------------------------------

    MAX_CONTEXT_CHARS = 6000

    @staticmethod
    def _is_execution_noise(content: str) -> bool:
        lowered = (content or "").lower()
        noisy_markers = (
            "execution stopped by user",
            "task execution timed out",
            "failure type: timeout",
            "origin: taskit_dag_executor",
            "origin: orchestrator:task_execution",
            "traceback (most recent call last)",
            "forkd command diagnostics",
            "resource busy",
            "sandbox command exited",
            "agent did not emit an odin-status block",
        )
        return any(marker in lowered for marker in noisy_markers)

    # Match markers on WORD boundaries, not as substrings. The old substring
    # `marker in text` fired on incidental English/DB words: "ui" is inside
    # "build"/"require"/"guidance", "form" is inside "platform", "ios" is inside
    # "scenarios" — so a browser/mobile proof section (1.5-5 KB) was appended to
    # nearly every backend/CLI task's prompt. Generic single tokens that carry
    # no reliable UI/mobile signal (ui, web, page, table, form, button, device,
    # phone) are dropped entirely; the rest match only as whole words/phrases.
    @staticmethod
    def _matches_marker(text: str, markers: tuple[str, ...]) -> bool:
        lowered = (text or "").lower()
        return any(
            re.search(rf"\b{re.escape(marker)}\b", lowered) for marker in markers
        )

    @staticmethod
    def _task_text_needs_mobile(text: str) -> bool:
        markers = (
            "mobile", "android", "ios", "iphone", "ipad", "react native",
            "expo", "emulator", "simulator", "apk",
        )
        return Orchestrator._matches_marker(text, markers)

    @staticmethod
    def _task_text_needs_browser(text: str) -> bool:
        markers = (
            "screenshot", "browser", "chrome", "devtools", "html", "css",
            "frontend", "dom", "visual", "render",
        )
        return Orchestrator._matches_marker(text, markers)

    def _select_task_mcps(self, task_text: str) -> list[str]:
        selected: list[str] = []
        configured = list(self.config.mcps)
        if "taskit" in configured:
            selected.append("taskit")
        if "chrome-devtools" in configured and self._task_text_needs_browser(task_text):
            selected.append("chrome-devtools")
        if "mobile" in configured and self._task_text_needs_mobile(task_text):
            selected.append("mobile")
        return selected

    def _build_task_context(self, task_id: str) -> str:
        """Build a comprehensive context block from the task's comment history.

        Collects all important comment types (reflections, summaries, human
        notes, Q&A, proof, agent output), filters noise, and emits a
        structured block within a token budget.  Replaces the narrower
        ``_build_reflection_context`` + ``_build_self_context`` pair.

        The summary comment acts as a checkpoint — most comment types are
        only collected post-summary.  Reflections (NEEDS_WORK + FAIL) cross
        the summary boundary because they're always relevant.

        Returns empty string if no meaningful context exists.
        """
        try:
            comments = self.task_mgr.get_comments(task_id)
        except Exception:
            return ""

        if not comments:
            return ""

        # ------ 1. Find latest summary index (checkpoint) ------
        latest_summary_idx = None
        for i in range(len(comments) - 1, -1, -1):
            if _comment_attr(comments[i], "comment_type") == "summary":
                latest_summary_idx = i
                break

        # ------ 2. Classify comments into priority buckets ------
        reflections = []       # NEEDS_WORK + FAIL, all comments (ignore summary boundary)
        summary_text = ""      # latest summary content
        summary_label = ""
        human_notes = []       # non-agent, non-system, post-summary
        qa_pairs = []          # question + reply, post-summary
        proof_items = []       # proof, post-summary
        latest_agent_output = ""  # latest agent status_update, post-summary

        for i, c in enumerate(comments):
            ctype = _comment_attr(c, "comment_type")
            raw_content = _comment_attr(c, "content", "")
            email = _comment_attr(c, "author_email", "")

            # --- Reflections: cross summary boundary ---
            if ctype == "reflection":
                content = _filter_comment_content(raw_content)
                if not content:
                    continue
                attachments = _comment_attr(c, "attachments", [])
                verdict = None
                for att in (attachments or []):
                    if isinstance(att, dict) and att.get("type") == "reflection":
                        verdict = (att.get("verdict") or "").upper()
                        break
                if verdict in ("NEEDS_WORK", "FAIL"):
                    reviewer = (
                        _comment_attr(c, "author_label")
                        or _comment_attr(c, "author_email", "reviewer")
                    )
                    reflections.append(f"[{verdict}] ({reviewer}): {content}")
                continue

            # --- Summary: capture latest ---
            if ctype == "summary" and i == latest_summary_idx:
                summary_text = _filter_comment_content(raw_content)
                summary_label = (
                    _comment_attr(c, "author_label")
                    or _comment_attr(c, "author_email", "AI Summary")
                )
                continue

            # --- Everything else: post-summary only ---
            is_post_summary = (
                latest_summary_idx is None or i > latest_summary_idx
            )
            if not is_post_summary:
                continue

            content = _filter_comment_content(raw_content)
            if not content or self._is_execution_noise(content):
                continue

            # Skip trace/debug attachment comments
            attachments = _comment_attr(c, "attachments", [])
            if attachments:
                att_list = attachments if isinstance(attachments, list) else []
                if any(
                    (isinstance(a, str) and a.startswith("debug:"))
                    for a in att_list
                ):
                    continue

            if ctype == "question":
                qa_pairs.append(f"[QUESTION]: {content}")
            elif ctype == "reply":
                qa_pairs.append(f"[REPLY]: {content}")
            elif ctype == "proof":
                if not self._is_execution_noise(content):
                    proof_items.append(content)
            elif ctype == "status_update" and email.endswith("@odin.agent"):
                if not self._is_execution_noise(content):
                    latest_agent_output = content  # keep overwriting; last one wins
            elif not email.endswith("@odin.agent") and email != "system@taskit":
                # Human note
                label = _comment_attr(c, "author_label") or email
                human_notes.append(f"- [{label}]: {content}")

        # ------ 3. Build sections in priority order ------
        sections = []

        if reflections:
            sections.append(
                ("## Previous Review Feedback", "\n\n".join(reflections))
            )

        if summary_text:
            sections.append(
                ("## Task Summary", f"(from {summary_label}):\n{summary_text}")
            )

        if human_notes:
            sections.append(
                ("## Human Notes", "\n".join(human_notes))
            )

        if qa_pairs:
            sections.append(("## Questions & Answers", "\n".join(qa_pairs)))

        if proof_items:
            # Cap each proof item
            capped = [p[:300] for p in proof_items[-2:]]
            sections.append(
                ("## Prior Proof of Work", "\n\n".join(capped))
            )

        if latest_agent_output:
            sections.append(
                ("## Previous Execution Output", latest_agent_output[:400])
            )

        if not sections:
            return ""

        # ------ 4. Enforce budget ------
        budget = self.MAX_CONTEXT_CHARS
        result_parts = []
        used = 0

        for header, body in sections:
            section_text = f"{header}\n{body}"
            section_len = len(section_text)

            if used + section_len <= budget:
                result_parts.append(section_text)
                used += section_len + 2  # account for \n\n join
            else:
                remaining = budget - used - len(header) - 20  # header + "[...truncated]"
                if remaining > 50:  # only include if we can fit something meaningful
                    truncated_body = body[:remaining] + "\n[...truncated]"
                    result_parts.append(f"{header}\n{truncated_body}")
                break  # budget exhausted

        return "\n\n".join(result_parts)

    # ------------------------------------------------------------------
    # summarize_task()
    # ------------------------------------------------------------------

    async def summarize_task(self, task_id: str) -> Dict[str, Any]:
        """Generate an AI summary of a task's comment history.

        Reads all comments, builds a summarize prompt, runs it through the
        task's assigned agent harness, and posts the result as a comment
        with comment_type="summary".  Clears the summarize_in_progress
        metadata flag when done.
        """
        try:
            full_id = self.task_mgr.resolve_task_id(task_id) or task_id
        except BackendUnreachable:
            full_id = task_id
        task = self.task_mgr.get_task(full_id)
        if not task:
            raise RuntimeError(f"Task not found: {task_id}")

        self._log.info("[task:%s] Summarize started", full_id)

        # Read all comments
        comments = self.task_mgr.get_comments(full_id)

        # Categorise and filter comments; separate prior summaries from activity
        all_filtered = []  # (index, ctype, formatted_line, raw_content)
        last_summary_idx = -1
        last_summary_content = None
        for i, c in enumerate(comments):
            attachments = _comment_attr(c, "attachments", [])
            ctype = _comment_attr(c, "comment_type", "")
            label = _comment_attr(c, "author_label", "") or _comment_attr(c, "author_email", "")
            content = _comment_attr(c, "content", "")
            created = _comment_attr(c, "created_at", "")

            # Skip trace/debug comments
            if attachments and any(
                (isinstance(a, str) and (a == "trace:execution_jsonl" or a.startswith("debug:")))
                for a in attachments
            ):
                continue
            if not content:
                continue

            ts = str(created)[:16] if created else ""
            line = f"[{ts}] [{ctype}] {label}: {content}"

            if ctype == "summary":
                last_summary_idx = len(all_filtered)
                last_summary_content = content
            all_filtered.append((i, ctype, line, content))

        # Build comment_lines: exclude summary comments, and if a prior summary
        # exists only include comments that came after it.
        comment_lines = []
        for idx, (_, ctype, line, _) in enumerate(all_filtered):
            if ctype == "summary":
                continue
            if last_summary_content is not None and idx <= last_summary_idx:
                continue
            comment_lines.append(line)

        if not comment_lines and last_summary_content is None:
            self._log.warning("[task:%s] No summarizable comments found", full_id)
            self._clear_summarize_flag(full_id)
            return {"task_id": full_id, "success": False, "error": "No summarizable comments"}

        # Build prompt — structure differs based on whether a prior summary exists
        header = (
            f"Task: {task.title}\n"
            f"Status: {task.status.value if hasattr(task.status, 'value') else task.status}\n"
            f"Assigned agent: {task.assigned_agent or 'unassigned'}\n\n"
            f"Description:\n{task.description or '(none)'}\n\n"
        )

        if last_summary_content is not None:
            activity_section = (
                "Prior summary (use as context about past activity — do NOT repeat verbatim):\n"
                + last_summary_content
                + "\n\n"
                + "New activity since last summary:\n"
                + ("\n".join(comment_lines) if comment_lines else "(no new activity)")
                + "\n\n"
            )
        else:
            activity_section = (
                "Comment history (chronological):\n"
                + "\n".join(comment_lines)
                + "\n\n"
            )

        instructions = (
            "Produce a structured markdown summary of this task. "
            "Use EXACTLY this format — output the markdown only, no preamble:\n\n"
            "## Task Summary\n\n"
            "### Execution History\n"
            "A markdown table with columns: | # | Agent | Model | Duration | Outcome | When |\n"
            "Extract execution sessions from the comments. Each execution attempt is one row.\n"
            "Parse agent identity from author labels like 'claude+sonnet-4@odin.agent' → Agent: claude, Model: sonnet-4.\n"
            "If duration or model is unknown, use '-'.\n\n"
            "### Key Events\n"
            "Timestamped bullet points of the most important events (max 8):\n"
            "- [HH:MM] Brief description of what happened\n"
            "Focus on: milestones reached, decisions made, errors encountered, deliverables produced.\n"
            "Skip routine status transitions — only include events that matter.\n\n"
            "### Outcome\n"
            "1-2 sentences: what was accomplished, what remains, any blockers.\n\n"
            "Rules:\n"
            "- Be specific and factual. Reference actual file names, modules, errors.\n"
            "- If there were no executions, omit the Execution History table.\n"
            "- If there are questions/replies in comments, note unresolved questions in Outcome.\n"
            "- Do not include filler phrases or disclaimers.\n"
            "- If a prior summary is provided, use it as context about what happened before. "
            "Generate a fresh comprehensive summary that covers BOTH the prior context and new activity. "
            "Do NOT copy or repeat the prior summary text."
        )

        prompt = header + activity_section + instructions

        forced_provider, forced_model = self._forced_provider()
        if forced_provider:
            agent_name = forced_provider
            agent_cfg = self.config.agents.get(agent_name)
        else:
            # Use the task's assigned agent, or fall back to base agent
            agent_name = task.assigned_agent or self.config.base_agent
            agent_cfg = self.config.agents.get(agent_name)
            if not agent_cfg:
                agent_name = self.config.base_agent
                agent_cfg = self.config.agents.get(agent_name)
        if not agent_cfg:
            self._clear_summarize_flag(full_id)
            raise RuntimeError(f"No agent config found for '{agent_name}'")

        self._assert_agent_cli_available(agent_name, action="summarize")
        harness = get_harness(agent_name, agent_cfg)

        # Resolve working dir
        working_dir = str(Path.cwd())
        if task.spec_id:
            spec = self.spec_store.load(task.spec_id)
            if spec and spec.metadata.get("working_dir"):
                working_dir = spec.metadata["working_dir"]

        context = {"working_dir": working_dir}

        # Pick model from task metadata, falling back to the assignee
        # agent's default from the TaskIt lineup (and then the agent
        # config) when nothing has been set explicitly.
        if forced_model:
            model = forced_model
        else:
            model = self._resolve_task_model(task, agent_name)
        if model:
            context["model"] = model

        try:
            result = await harness.execute(prompt, context)
        except Exception as exc:
            self._log.error("[task:%s] Summarize harness failed: %s", full_id, exc, exc_info=True)
            self._clear_summarize_flag(full_id)
            return {"task_id": full_id, "success": False, "error": str(exc)}

        # Extract clean text from structured output
        summary_text = self._extract_agent_text(result.output or "").strip()
        # Strip any ODIN-STATUS envelope if the harness added one
        clean_text, _, _ = self._parse_envelope(summary_text)
        summary_text = clean_text.strip()

        if not summary_text:
            self._log.warning("[task:%s] Summarize produced empty output", full_id)
            self._clear_summarize_flag(full_id)
            return {"task_id": full_id, "success": False, "error": "Empty summary output"}

        # Post as summary comment
        self.task_mgr.add_comment(
            task_id=full_id,
            author="odin",
            content=summary_text,
            comment_type="summary",
        )

        # Clear the in-progress flag
        self._clear_summarize_flag(full_id)

        self._log.info("[task:%s] Summary posted (%d chars)", full_id, len(summary_text))
        return {"task_id": full_id, "success": True, "summary": summary_text}

    def _clear_summarize_flag(self, task_id: str) -> None:
        """Clear the summarize_in_progress metadata flag on a task."""
        try:
            task = self.task_mgr.get_task(task_id)
            if task and task.metadata and task.metadata.get("summarize_in_progress"):
                task.metadata.pop("summarize_in_progress", None)
                self.task_mgr.update_task(task)
        except Exception:
            self._log.warning(
                "[task:%s] Failed to clear summarize_in_progress flag", task_id,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # DAG validation
    # ------------------------------------------------------------------

    def _validate_dag(self, task_ids: List[str]) -> None:
        """Validate that task dependencies form a DAG (no cycles).

        Raises RuntimeError with the cycle path if a cycle is detected.
        """
        from odin.dag import detect_cycle

        cycle = detect_cycle(task_ids, self._task_resolver)
        if cycle is not None:
            tasks_by_id = {
                tid: self.task_mgr.get_task(tid)
                for tid in cycle
                if self.task_mgr.get_task(tid) is not None
            }
            titles = [
                tasks_by_id[c].title
                for c in cycle
                if c in tasks_by_id
            ]
            raise RuntimeError(
                f"Dependency cycle detected: {' → '.join(titles)}"
            )

    def _task_resolver(self, task_id: str) -> Optional[Task]:
        """Resolve a task ID to a Task object (adapter for dependencies module)."""
        return self.task_mgr.get_task(task_id)

    # ------------------------------------------------------------------
    # MCP config generation
    # ------------------------------------------------------------------

    def _get_mcp_env(self, task_id: str, agent_name: str, model: Optional[str] = None) -> dict:
        """Build the env dict for taskit-mcp, shared across all CLI formats."""
        auth_token = ""
        if (
            self._backend
            and hasattr(self._backend, "_client")
            and self._backend._client.auth
            and hasattr(self._backend._client.auth, "get_token")
        ):
            try:
                auth_token = self._backend._client.auth.get_token()
            except Exception:
                self._log.warning(
                    "[task:%s] Could not get auth token for MCP config",
                    task_id,
                )
        return {
            "TASKIT_URL": self.config.taskit.base_url,
            "TASKIT_AUTH_TOKEN": auth_token,
            "TASKIT_TASK_ID": str(task_id),
            "TASKIT_AUTHOR_EMAIL": TaskManager._format_actor_email(agent_name, model),
            "TASKIT_AUTHOR_LABEL": TaskManager._format_actor_label(agent_name, model),
        }

    def _generate_mcp_config(
        self, task_id: str, agent_name: str, log_dir: Path,
        working_dir: Optional[str] = None, model: Optional[str] = None,
        mcps: Optional[List[str]] = None,
        mcp_env_override: Optional[Dict[str, str]] = None,
        chrome_executable_path: Optional[str] = None,
        chrome_args: Optional[List[str]] = None,
        chrome_isolated: bool = False,
        chrome_browser_url: Optional[str] = None,
    ) -> Optional[str]:
        """Generate per-CLI MCP config files for the agent.

        Merges server entries from all configured MCP packages (controlled
        by ``self.config.mcps``) into a single config file per CLI format.

        For Claude, returns the config file path (for --mcp-config flag).
        For all other CLIs, writes to working_dir and returns None (auto-discovery).
        Returns None if no MCP servers are configured.
        """
        mcps = self.config.mcps if mcps is None else mcps
        has_taskit = "taskit" in mcps and self.config.taskit
        has_mobile = "mobile" in mcps
        has_chrome_devtools = "chrome-devtools" in mcps
        cd_headless = bool(
            self.config.chrome_devtools and self.config.chrome_devtools.headless
        )
        cd_kwargs = {
            "headless": cd_headless,
            "executable_path": chrome_executable_path,
            "chrome_args": chrome_args,
            "isolated": chrome_isolated,
            "browser_url": chrome_browser_url,
        }

        if not has_taskit and not has_mobile and not has_chrome_devtools:
            return None

        from odin.mcps.taskit_mcp.config import (
            MCP_CONFIG_MAP, server_entry as taskit_server_entry,
        )

        # Build taskit env only when taskit is configured. forkd callers pass a
        # guest-reachable TASKIT_URL while preserving the normal auth/task identity.
        env = mcp_env_override or (self._get_mcp_env(task_id, agent_name, model=model) if has_taskit else {})

        # --- Codex: CLI flag injection (no config file) ---
        # Codex is handled separately because it uses -c flags, not config files.
        # The actual flags are injected via context["mcp_env"] and
        # context["mobile_mcp_enabled"] in the harness.
        if agent_name == "codex":
            # Codex writes TOML; merge taskit section(s) into TOML lines
            lines = []
            if has_taskit:
                lines.extend([
                    "[mcp_servers.taskit]",
                    'command = "taskit-mcp"',
                    "",
                    "[mcp_servers.taskit.env]",
                ])
                for k, v in env.items():
                    lines.append(f'{k} = "{v}"')
            if has_mobile:
                if lines:
                    lines.append("")
                lines.extend([
                    "[mcp_servers.mobile]",
                    'command = "npx"',
                    'args = ["-y", "@mobilenext/mobile-mcp@latest"]',
                ])
            if has_chrome_devtools:
                if lines:
                    lines.append("")
                cd_args = ["-y", "chrome-devtools-mcp@latest"]
                if chrome_browser_url:
                    # Connect to a remote (host-side) browser; launch flags conflict.
                    cd_args.extend(["--browserUrl", chrome_browser_url])
                else:
                    if cd_headless:
                        cd_args.append("--headless")
                    if chrome_executable_path:
                        cd_args.extend(["--executablePath", chrome_executable_path])
                    if chrome_isolated:
                        cd_args.append("--isolated")
                    for chrome_arg in chrome_args or []:
                        cd_args.append(f"--chromeArg={chrome_arg}")
                args_toml = "[" + ", ".join(f'"{a}"' for a in cd_args) + "]"
                lines.extend([
                    "[mcp_servers.chrome-devtools]",
                    'command = "npx"',
                    f"args = {args_toml}",
                ])
            content = "\n".join(lines) + "\n" if lines else ""
            if not content:
                return None
            wd = Path(working_dir) if working_dir else Path.cwd()
            config_path = wd / ".codex/config.toml"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(content)
            self._log.debug(
                "[task:%s] Generated Codex MCP config at %s", task_id, config_path,
            )
            return None

        # --- OpenCode agents (minimax, glm): merge mcp + permission dicts ---
        # opencode reads TaskIt MCP identity from opencode.json. In forkd mode
        # the TASKIT_URL may be a placeholder that ForkdHarness rewrites to the
        # guest-reachable proxy URL before staging the config.
        if agent_name in ("minimax", "glm"):
            from odin.mcps.taskit_mcp.config import tool_names as taskit_tool_names
            mcp_servers: Dict = {}
            permission: Dict = {}
            if has_taskit:
                entry = taskit_server_entry(agent_name, env)
                mcp_servers.update(entry)
                permission.update({t: "allow" for t in taskit_tool_names()})
            if has_mobile:
                from odin.mcps.mobile_mcp.config import (
                    server_fragment as mobile_fragment,
                    mobile_tool_names, _opencode_permissions,
                )
                mcp_servers.update(mobile_fragment(agent_name))
                permission.update(_opencode_permissions())
            if has_chrome_devtools:
                from odin.mcps.chrome_devtools_mcp.config import (
                    server_fragment as cd_fragment,
                    _opencode_permissions as cd_opencode_permissions,
                )
                mcp_servers.update(cd_fragment(agent_name, **cd_kwargs))
                permission.update(cd_opencode_permissions())
            # NOTE: do NOT blanket-allow "external_directory" on the HOST. It
            # unblocks git in linked worktrees (gitdir lives under the main
            # repo's .git/worktrees/) but also lets agents roam the host
            # filesystem — macOS privacy prompts on ~/Desktop/~/Downloads
            # (finding F25).
            # In a VM sandbox the calculus flips: the guest filesystem only
            # contains what the harness explicitly mounted (worktree + repo
            # .git + read-only cred files), so the grant cannot reach anything
            # the sandbox didn't already expose — and without it opencode
            # auto-rejects worktree git/tool calls mid-run (F35: killed #98's
            # lint pass after the work was already done).
            from odin.harnesses.registry import _resolve_sandbox_mode

            agent_cfg = self.config.agents.get(agent_name)
            if agent_cfg and _resolve_sandbox_mode(agent_cfg) in ("microsandbox", "forkd"):
                permission.setdefault("external_directory", "allow")
            content = json.dumps({"permission": permission, "mcp": mcp_servers}, indent=2)
            wd = Path(working_dir) if working_dir else Path.cwd()
            config_path = wd / "opencode.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(content)
            self._log.debug(
                "[task:%s] Generated %s MCP config at %s", task_id, agent_name, config_path,
            )
            return None

        # --- mcpServers-based agents (claude, gemini, kilocode) ---
        servers: Dict = {}
        if has_taskit:
            servers.update(taskit_server_entry(agent_name, env))
        if has_mobile:
            from odin.mcps.mobile_mcp.config import server_fragment as mobile_fragment
            servers.update(mobile_fragment(agent_name))
        if has_chrome_devtools:
            from odin.mcps.chrome_devtools_mcp.config import server_fragment as cd_fragment
            servers.update(cd_fragment(agent_name, **cd_kwargs))

        if not servers:
            return None

        content = json.dumps({"mcpServers": servers}, indent=2)

        rel_path = MCP_CONFIG_MAP.get(agent_name)
        if agent_name == "claude" or not rel_path:
            # Claude supports --mcp-config: write to log_dir, return path
            config_path = log_dir / f"mcp_{task_id}.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(content)
            self._log.debug(
                "[task:%s] Generated Claude MCP config at %s", task_id, config_path,
            )
            return str(config_path)
        else:
            # All other CLIs: write to working_dir for auto-discovery
            wd = Path(working_dir) if working_dir else Path.cwd()
            config_path = wd / rel_path
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(content)
            self._log.debug(
                "[task:%s] Generated %s MCP config at %s",
                task_id, agent_name, config_path,
            )
            return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _fetch_quota(self) -> Dict[str, Dict[str, float]]:
        """Fetch quota data from harness_usage_status for all known agents.

        Returns {agent_name: {"usage_pct": float, "remaining_pct": float}}.
        Returns {} if the package is not installed or fetch fails (graceful degradation).
        """
        try:
            from harness_usage_status.config import load_config as load_hus_config
            from harness_usage_status.providers.registry import get_provider
        except ImportError:
            return {}

        try:
            hus_config = load_hus_config()
            provider_configs = hus_config.get_provider_configs()
            quota_data: Dict[str, Dict[str, float]] = {}

            for agent_name, provider_name in QUOTA_PROVIDER_MAP.items():
                if agent_name not in self.config.enabled_agents():
                    continue
                if provider_name not in provider_configs:
                    continue
                try:
                    provider = get_provider(provider_name, provider_configs[provider_name])
                    usage_info = await provider.get_usage()
                    usage_pct = usage_info.usage_pct if usage_info.usage_pct is not None else 0.0
                    remaining_pct = round(100.0 - usage_pct, 1)
                    quota_data[agent_name] = {
                        "usage_pct": usage_pct,
                        "remaining_pct": remaining_pct,
                    }
                except Exception:
                    continue

            return quota_data
        except Exception:
            return {}

    def _normalize_capabilities(self, agent_name: str, caps: Optional[List[str]]) -> List[str]:
        normalized = set(caps or [])
        cfg = self.config.agents.get(agent_name)
        if cfg:
            normalized.update(cfg.capabilities)
        # Many coding-capable CLIs use shell/file tools under the hood even if
        # older routing metadata omitted explicit tool-style capabilities.
        if "coding" in normalized:
            normalized.update({"run_shell_command", "read_file", "write_file"})
        return sorted(normalized)

    async def _build_available_agents(
        self,
        quota: Optional[Dict[str, Dict[str, float]]] = None,
        routing_config: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Build the available agents list for planning prompts.

        When routing_config is available (from API), uses it as the primary
        source for agent/model data. Falls back to config-based agents.

        W10.4: an agent marked ``enabled=False`` on the board is dropped
        here as a belt-and-braces filter, even if a backend change ever
        ships without filtering at the endpoint. The previous symptom
        (every task landing on the base agent because nobody was enrolled)
        gets caught here too — empty roster → the caller renders the
        ``settings_path`` hint from ``routing_config`` so the operator
        gets the same one-liner that ``/boards/{id}/agents/`` would have
        shown in the UI.
        """
        available = []

        if routing_config and "agents" in routing_config:
            # API-sourced: richer model info with enabled/disabled state
            for agent_data in routing_config["agents"]:
                # W10.4: roster-disabled agents never reach the planner
                # even if a future API change forgets to filter them.
                if agent_data.get("enabled") is False:
                    continue
                name = agent_data["name"]
                cfg = self.config.agents.get(name)
                if not cfg:
                    continue
                h = get_harness(name, cfg)
                if not await h.is_available():
                    continue
                models_for_prompt = []
                for m in agent_data.get("models", []):
                    if m.get("enabled", True):
                        models_for_prompt.append({
                            "name": m["name"],
                            "description": m.get("description", ""),
                            "is_default": m.get("is_default", False),
                        })
                agent_info: Dict[str, Any] = {
                    "name": name,
                    "capabilities": self._normalize_capabilities(name, agent_data.get("capabilities", [])),
                    "cost_tier": agent_data.get("cost_tier", "medium"),
                    "default_model": agent_data.get("default_model"),
                    "premium_model": agent_data.get("premium_model"),
                    "models": models_for_prompt,
                }
                if quota and name in quota:
                    agent_info["usage_pct"] = quota[name]["usage_pct"]
                    agent_info["remaining_pct"] = quota[name]["remaining_pct"]
                available.append(agent_info)
        else:
            # Fallback: config-based agents
            for name, cfg in self.config.enabled_agents().items():
                h = get_harness(name, cfg)
                if await h.is_available():
                    models_for_prompt = {}
                    for model_name, note in cfg.models.items():
                        models_for_prompt[model_name] = note if note else model_name
                    agent_info = {
                        "name": name,
                        "capabilities": self._normalize_capabilities(name, cfg.capabilities),
                        "cost_tier": cfg.cost_tier.value,
                        "models": models_for_prompt,
                    }
                    if quota and name in quota:
                        agent_info["usage_pct"] = quota[name]["usage_pct"]
                        agent_info["remaining_pct"] = quota[name]["remaining_pct"]
                    available.append(agent_info)
        return available

    async def _decompose(
        self,
        prompt: str,
        working_dir: str,
        spec_id: str,
        stream_callback: Optional[Callable[[str], None]] = None,
        plan_path: Optional[str] = None,
    ) -> TaskResult:
        """Dispatch the plan prompt to the base agent harness.

        The agent writes its plan JSON to the plan_path specified in the
        prompt.  This method handles only the harness dispatch — prompt
        building and plan reading happen in ``plan()``.

        In "auto" mode, ``stream_callback`` is called per chunk for
        terminal display.  In "quiet" mode, no callback is provided.

        Returns the TaskResult from the harness for trace capture.
        """
        base_name, model = self._planning_agent_model()
        base_cfg = self.config.agents.get(base_name)
        if not base_cfg:
            raise RuntimeError(f"Planning agent '{base_name}' not found in config")

        self._assert_agent_cli_available(base_name, action="planning")

        harness = get_harness(base_name, base_cfg)

        self.logger.log(
            action="decompose_started", agent=base_name, input_prompt=prompt[:500]
        )

        log_dir = Path(self.config.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        output_file = str(log_dir / f"plan_{spec_id}.out")
        trace_file = str(log_dir / f"plan_{spec_id}.trace.jsonl")
        context = {
            "working_dir": working_dir,
            "output_file": output_file,
            "trace_file": trace_file,
            "validate_status": False,
        }
        if plan_path:
            context["forkd_mapped_output_files"] = [plan_path]
        if model:
            context["model"] = model
        start_ms = time.monotonic() * 1000

        if stream_callback and harness.build_execute_command(prompt, context) is not None:
            # Stream output chunks to terminal only for harnesses that expose a
            # native streaming CLI path. Wrapped sandbox harnesses execute as a
            # one-shot task and must preserve TaskResult success/error details.
            chunks: List[str] = []
            trace_path = Path(trace_file)
            with trace_path.open("w") as tf:
                async for chunk in harness.execute_streaming(prompt, context):
                    tf.write(chunk)
                    stream_callback(chunk)
                    chunks.append(chunk)
            duration_ms = time.monotonic() * 1000 - start_ms
            result = TaskResult(
                success=True,
                output="".join(chunks),
                duration_ms=duration_ms,
                agent=base_name,
            )
        else:
            result = await harness.execute(prompt, context)
            if stream_callback and result.output:
                stream_callback(result.output)
            if not result.success:
                raise RuntimeError(
                    f"Decomposition failed: {result.error or 'unknown error'}"
                )

        self.logger.log(
            action="decompose_completed",
            agent=base_name,
        )
        result.agent = base_name
        return result

    def _parse_json_array(self, text: str) -> List[Dict[str, Any]]:
        """Extract a JSON array from agent output (may contain markdown fences)."""
        def _try_parse(candidate: str) -> Optional[List[Dict[str, Any]]]:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                pass
            repaired = self._repair_json_strings(candidate)
            if repaired != candidate:
                try:
                    parsed = json.loads(repaired)
                    if isinstance(parsed, list):
                        return parsed
                except json.JSONDecodeError:
                    pass
            return None

        # Try direct parse first
        text = text.strip()
        parsed = _try_parse(text)
        if parsed is not None:
            return parsed

        # Try extracting from markdown code block
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            parsed = _try_parse(match.group(1))
            if parsed is not None:
                return parsed

        # Try finding array brackets
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            parsed = _try_parse(text[start : end + 1])
            if parsed is not None:
                return parsed

        raise RuntimeError(f"Could not parse sub-tasks JSON from output: {text[:300]}")

    @staticmethod
    def _repair_json_strings(text: str) -> str:
        """Escape raw newlines inside quoted JSON strings.

        Some agent/tool flows produce JSON-like output with literal newline
        characters inside quoted strings. Strict ``json.loads`` rejects that
        even though the structure is otherwise valid. This normalizes those
        strings without touching newlines outside of quotes.
        """
        out: List[str] = []
        in_string = False
        escaped = False

        for ch in text:
            if in_string:
                if escaped:
                    out.append(ch)
                    escaped = False
                    continue
                if ch == "\\":
                    out.append(ch)
                    escaped = True
                    continue
                if ch == '"':
                    out.append(ch)
                    in_string = False
                    continue
                if ch == "\n":
                    out.append("\\n")
                    continue
                if ch == "\r":
                    out.append("\\r")
                    continue
                if ch == "\t":
                    out.append("\\t")
                    continue
                out.append(ch)
                continue

            out.append(ch)
            if ch == '"':
                in_string = True

        return "".join(out)

    async def _is_available_cached(self, name: str, cfg) -> bool:
        """Check agent availability with caching."""
        if name not in self._availability_cache:
            h = get_harness(name, cfg)
            self._availability_cache[name] = await h.is_available()
        return self._availability_cache[name]

    async def _route_task(
        self,
        required_caps: List[str],
        complexity: str,
        suggested: Optional[str],
        quota: Optional[Dict[str, Dict[str, float]]],
        routing_config: Optional[Dict[str, Any]] = None,
        suggested_model: Optional[str] = None,
    ) -> Tuple[str, Optional[str], str, Dict[str, Any]]:
        """Unified agent+model selection respecting LLM suggestions.

        Returns ``(agent, model, routing_reasoning, assignment_reason)``.

        ``assignment_reason`` is a structured dict (see
        :meth:`_build_assignment_reason`) that the human-facing UI
        renders next to the assignee avatar. The router never sets
        ``override=true``; that's the taskit-backend's job when a
        human reassigns a task after dispatch.

        When routing_config is available (from API), uses it as the source
        of enabled agents and models. Falls back to config-based routing.

        1. If the LLM suggested an agent (and optionally a model), try that first.
        2. Otherwise, collect enabled models from all viable agents,
           group by cost tier, pick the cheapest tier, random.choice().
        3. Upgrade to premium_model for high-complexity tasks.

        Raises RuntimeError if no viable route is found.
        """
        if routing_config and "agents" in routing_config:
            return await self._route_task_api(
                required_caps, complexity, suggested, quota, routing_config,
                suggested_model=suggested_model,
            )
        return await self._route_task_config(
            required_caps, complexity, suggested, quota,
            suggested_model=suggested_model,
        )

    def _build_assignment_reason(
        self,
        *,
        agent: str,
        model: Optional[str],
        rule: str,
        reasoning: str,
        viable_routes: Optional[List[Tuple[str, Optional[str], str]]],
        stats_by_name: Dict[str, AgentRoutingStats],
        picked_tier: str,
    ) -> Dict[str, Any]:
        """Assemble the structured dict the human-facing UI consumes.

        Shape (stable contract — the frontend reads every key):

          * ``agent`` / ``model`` — the picked route, mirrored so the
            UI doesn't have to cross-reference routing_reasoning.
          * ``rule`` — ``"suggested-agent" | "suggested-model" |
            "history" | "static-fallback" | "escalated"``. Drives the
            label rendered next to the assignee ("Auto" vs "Override").
          * ``reason`` — the same one-line string as
            ``routing_reasoning``. Single source of truth.
          * ``override`` — always False at dispatch. The taskit-backend
            flips this when a human reassigns the task after dispatch.
          * ``cheaper_alternatives`` — viable agents strictly cheaper
            than the pick, with their success_rate and a one-line reason
            explaining why they lost. Empty list when nothing cheaper
            was viable (the cheapest-tier pick). Filtered by viability
            so we never list agents that couldn't have been chosen.
          * ``twin_consensus`` — left ``None`` at dispatch. The W5.5
            twins service computes and exposes ``task.twins`` in the
            detail API; the UI derives the consensus line from there
            when present. Stamping it here would require an extra
            backend call on the hot path — Default First omits it.

        All fields are populated deterministically from the same
        measured-history math that already powers ``routing_reasoning`` —
        no model call, no planner cost.
        """
        tier_order = {"low": 0, "medium": 1, "high": 2}
        picked_rank = tier_order.get(picked_tier, 1)
        cheaper_alternatives: List[Dict[str, Any]] = []

        if viable_routes and picked_rank > 0:
            for route in viable_routes:
                route_name, route_model, route_tier = route
                if route_name == agent:
                    continue
                if tier_order.get(route_tier, 1) >= picked_rank:
                    continue
                stats = stats_by_name.get(route_name) if stats_by_name else None
                success_rate = stats.success_rate if stats else None
                threshold = getattr(stats, "_threshold", None) if stats else None
                if stats and stats.success_rate < 0.5:
                    reason = (
                        f"success_rate {stats.success_rate:.2f} below 0.50"
                    )
                elif stats and stats.success_rate < 0.8:
                    reason = (
                        f"success_rate {stats.success_rate:.2f} — "
                        f"pick had stronger history"
                    )
                else:
                    reason = "cheaper tier, picked agent had stronger overall fit"
                cheaper_alternatives.append({
                    "agent": route_name,
                    "model": route_model,
                    "tier": route_tier,
                    "success_rate": success_rate,
                    "median_tokens": stats.median_tokens if stats else None,
                    "reason": reason,
                })

        return {
            "agent": agent,
            "model": model,
            "rule": rule,
            "reason": reasoning,
            "override": False,
            "cheaper_alternatives": cheaper_alternatives,
            "twin_consensus": None,
        }

    def _pick_from_viable_routes(
        self,
        viable_routes: List[Tuple[str, Optional[str], str]],
        stats_by_name: Dict[str, AgentRoutingStats],
    ) -> Tuple[Tuple[str, Optional[str], str], object, str]:
        """Pick one (agent, model, tier) from the full viable pool using
        measured history when available, falling back to uniform random
        within the cheapest tier.

        Returns ``((agent, model, tier), decision, reasoning_suffix)``
        where ``decision`` is the underlying :class:`RoutingDecision`
        or :class:`StaticFallback` instance — exposed so the caller can
        stamp ``assignment_reason.rule`` truthfully (history vs
        static-fallback vs escalation).

        - **History-driven path** (Default First when we have signal):
          calls :func:`odin.agent_routing.suggest_routing` over the
          *full* viable-pool names — not just the cheapest tier — so
          the suggester can escalate across tiers when the cheap
          candidates fail. If the suggester returns a
          :class:`RoutingDecision`, the picked name wins and the
          decision's reason is returned as ``reasoning_suffix``. This
          is the fix for the production gap where escalation was
          unit-tested in the pure suggester but never fired because
          the orchestrator pre-filtered to the cheapest tier.

        - **Static fallback path** (thin history, backend unavailable,
          or no qualifier cleared the threshold): the prior behavior —
          uniform random within the cheapest viable tier — preserves
          the tier-distribution contract that older tests pin. The
          returned reason says "static fallback" so routing_reasoning
          is auditable.

        ``stats_by_name`` may be empty; in that case we go straight to
        the fallback. Defensive ``Exception`` handling around the
        suggester mirrors the backend's
        :meth:`_fetch_agent_stats` contract: any failure returns the
        fallback silently.
        """
        if not viable_routes:
            raise ValueError("_pick_from_viable_routes needs at least one route")

        # Identify the cheapest viable tier for the static fallback
        # bucket. The history-driven path does not care about tiers —
        # it ranks every qualified agent regardless of cost.
        tier_order = {"low": 0, "medium": 1, "high": 2}
        cheapest_tier = min(
            viable_routes, key=lambda r: tier_order.get(r[2], 1)
        )[2]
        tier_candidates = [r for r in viable_routes if r[2] == cheapest_tier]

        # Empty/wholly-unavailable backend — fall back; signature matches
        # the static path exactly so a downstream refactor doesn't change
        # the call shape.
        if not stats_by_name:
            chosen = random.choice(tier_candidates)
            return (
                chosen,
                RoutingStaticFallback(
                    reason="no agent stats available",
                    min_samples=DEFAULT_MIN_SAMPLES,
                    threshold=DEFAULT_SUCCESS_THRESHOLD,
                ),
                f"(static fallback: no agent stats available; "
                f"random among {len(tier_candidates)} {chosen[2].upper()}-tier route"
                f"{'s' if len(tier_candidates) != 1 else ''})",
            )

        # Pass the FULL viable pool to the suggester. This is the
        # correction: the suggester can now see — and pick — agents in
        # a higher tier when every cheap-tier agent is below the
        # success threshold. Pre-filtering to tier_candidates here
        # would re-introduce the production gap.
        candidate_names = sorted({r[0] for r in viable_routes})
        try:
            decision = suggest_routing(
                candidate_names,
                stats_by_name,
            )
        except Exception as exc:
            chosen = random.choice(tier_candidates)
            return (
                chosen,
                RoutingStaticFallback(
                    reason=f"suggester error: {exc}",
                    min_samples=DEFAULT_MIN_SAMPLES,
                    threshold=DEFAULT_SUCCESS_THRESHOLD,
                ),
                f"(static fallback: suggester error; random among "
                f"{len(tier_candidates)} {chosen[2].upper()}-tier route"
                f"{'s' if len(tier_candidates) != 1 else ''})",
            )

        if isinstance(decision, RoutingStaticFallback):
            chosen = random.choice(tier_candidates)
            return (
                chosen,
                decision,
                f"(static fallback: {decision.reason}; "
                f"random among {len(tier_candidates)} {chosen[2].upper()}-tier route"
                f"{'s' if len(tier_candidates) != 1 else ''})",
            )

        # RoutingDecision — picked agent name wins.
        assert isinstance(decision, RoutingDecision)
        for route in viable_routes:
            if route[0] == decision.picked:
                chosen = route
                break
        else:
            # Shouldn't happen — the suggester only names candidates we
            # passed in. Fall back defensively rather than crashing.
            chosen = tier_candidates[0]
        return (chosen, decision, f"({decision.reason})")

    async def _route_task_api(
        self,
        required_caps: List[str],
        complexity: str,
        suggested: Optional[str],
        quota: Optional[Dict[str, Dict[str, float]]],
        routing_config: Dict[str, Any],
        suggested_model: Optional[str] = None,
    ) -> Tuple[str, Optional[str], str, Dict[str, Any]]:
        """Route using API-sourced agent/model data.

        Returns ``(agent, model, routing_reasoning, assignment_reason)``.

        W10.4 follow-on: refuse to silently re-route when the planner
        names an agent that is not on this board's roster. The
        ``/boards/{id}/routing-config/`` endpoint already filters
        roster-disabled agents out, so a planner-named agent that is
        absent from ``agents_by_name`` is, by construction, an agent
        the operator disabled on this board (board 6's symptom). Raising
        here surfaces the named agent + the settings URL — the operator
        fixes the cause (settings) rather than chasing a silent
        cheapest-tier fallback. Symmetric with
        ``tasks.dag_executor.AgentNotEnabledOnBoard`` on the dispatch
        side.
        """
        agents_data = routing_config["agents"]

        # Build index: agent_name -> agent_data
        agents_by_name = {a["name"]: a for a in agents_data}

        # Settings URL the planner embed will display. The endpoint
        # carries this for every fresh board; fall back to a generic
        # hint when an older / synthetic routing_config omits it.
        settings_path = routing_config.get("settings_path") or ""

        # Phase 0: refuse silent fallback when the planner names an
        # agent that isn't on this board's roster. Two flag shapes:
        #   * absent from agents_by_name (the endpoint's current
        #     contract — roster-disabled agents are stripped)
        #   * present but enabled=False (defensive — if the endpoint
        #     ever stops filtering, the planner-side guard still fires)
        if suggested:
            in_roster = suggested in agents_by_name
            flagged_disabled = (
                in_roster
                and agents_by_name[suggested].get("enabled") is False
            )
            if not in_roster or flagged_disabled:
                raise SuggestedAgentDisabled(
                    agent=suggested,
                    settings_url=settings_path,
                )

        # Phase 1: honour the suggested agent if possible
        if suggested and suggested in agents_by_name:
            agent_data = agents_by_name[suggested]
            cfg = self.config.agents.get(suggested)
            if cfg and await self._is_available_cached(suggested, cfg):
                caps = self._normalize_capabilities(suggested, agent_data.get("capabilities", []))
                if not required_caps or all(c in caps for c in required_caps):
                    if not self._over_quota(suggested, quota, complexity):
                        enabled_models = [
                            m["name"] for m in agent_data.get("models", [])
                            if m.get("enabled", True)
                        ]
                        # Honour planner's suggested_model if it's enabled
                        planner_chose_model = suggested_model and suggested_model in enabled_models
                        if planner_chose_model:
                            model = suggested_model
                        else:
                            model = agent_data.get("default_model")
                            if model and model not in enabled_models and enabled_models:
                                model = enabled_models[0]
                            elif not model and enabled_models:
                                model = enabled_models[0]
                        # Only auto-upgrade when the router picked the model.
                        # When the planner explicitly chose a model, respect it —
                        # the planner already knows the task complexity.
                        reason_suffix = ""
                        if not planner_chose_model:
                            model, reason_suffix = self._maybe_upgrade_model_api(
                                suggested, model, complexity, agent_data
                            )
                        src = "suggested model" if planner_chose_model else "suggested agent"
                        reasoning = f"Routed to {suggested}/{model} ({src}{reason_suffix})"
                        rule = "suggested-model" if planner_chose_model else "suggested-agent"
                        # Phase 1 honours the planner — no cheaper
                        # alternatives listed (the planner already decided).
                        assignment_reason = self._build_assignment_reason(
                            agent=suggested,
                            model=model,
                            rule=rule,
                            reasoning=reasoning,
                            viable_routes=None,
                            stats_by_name=None,
                            picked_tier=agent_data.get("cost_tier", "medium"),
                        )
                        return (suggested, model, reasoning, assignment_reason)

        # Phase 2: collect viable routes from enabled models
        tier_order = {"low": 0, "medium": 1, "high": 2}
        viable_routes: List[Tuple[str, str, str]] = []  # (agent, model, tier)

        for agent_data in agents_data:
            name = agent_data["name"]
            cfg = self.config.agents.get(name)
            if not cfg:
                continue
            if not await self._is_available_cached(name, cfg):
                continue
            caps = self._normalize_capabilities(name, agent_data.get("capabilities", []))
            if required_caps and not all(c in caps for c in required_caps):
                continue
            if self._over_quota(name, quota, complexity):
                continue
            tier = agent_data.get("cost_tier", "medium")
            for m in agent_data.get("models", []):
                if m.get("enabled", True):
                    viable_routes.append((name, m["name"], tier))

        if not viable_routes:
            tried = [a["name"] for a in agents_data]
            raise RuntimeError(
                f"No viable route for task (caps={required_caps}, "
                f"complexity={complexity}, suggested={suggested}). "
                f"Tried agents: {tried}"
            )

        # History-driven pick over the FULL viable pool so the
        # suggester can escalate across tiers when the cheapest tier
        # has no qualifier. Static fallback (thin history / no
        # qualifier) still distributes within the cheapest tier.
        # Defensive: any backend error → {} → fallback path. Default
        # First — never break plan over a stats fetch blip.
        try:
            stats = self._fetch_agent_stats()
        except Exception:
            stats = {}
        chosen, decision, route_suffix = self._pick_from_viable_routes(
            viable_routes, stats
        )
        agent, model, tier = chosen

        # Premium upgrade for high-complexity tasks
        agent_data = agents_by_name.get(agent, {})
        model, reason_suffix = self._maybe_upgrade_model_api(agent, model, complexity, agent_data)

        # Surface the cheapest viable tier in the reasoning so the
        # operator can see whether the pick stayed in tier (cheap
        # path) or escalated (history path). When pick tier ≠ cheapest
        # tier, the reasoning explicitly says "escalated" so the
        # audit trail tells the truth.
        cheapest_tier = min(
            viable_routes, key=lambda r: tier_order.get(r[2], 1)
        )[2]
        cheap_tier_names = sorted(
            {r[0] for r in viable_routes if r[2] == cheapest_tier}
        )
        escalated = tier != cheapest_tier
        if escalated:
            pick_tier_names = sorted(
                {r[0] for r in viable_routes if r[2] == tier}
            )
            reasoning = (
                f"Routed to {agent}/{model} (escalated from "
                f"{cheapest_tier.upper()} tier to {tier.upper()} tier; "
                f"history-driven; cheapest-tier pool was "
                f"{', '.join(cheap_tier_names)}; pick-tier pool "
                f"{', '.join(pick_tier_names)}{route_suffix}{reason_suffix})"
            )
        else:
            reasoning = (
                f"Routed to {agent}/{model} ({tier.upper()} tier, "
                f"chosen from {len(cheap_tier_names)} viable "
                f"{tier.upper()}-tier route{'s' if len(cheap_tier_names) != 1 else ''}: "
                f"{', '.join(cheap_tier_names)}{route_suffix}{reason_suffix})"
            )

        # Determine the rule from the decision + escalation signal.
        # The decision object itself distinguishes RoutingDecision
        # (history-driven) from StaticFallback — escalation is a
        # separate dimension we add when pick tier ≠ cheapest tier.
        if isinstance(decision, RoutingDecision) and escalated:
            rule = "escalated"
        elif isinstance(decision, RoutingDecision):
            rule = "history"
        else:
            rule = "static-fallback"

        assignment_reason = self._build_assignment_reason(
            agent=agent,
            model=model,
            rule=rule,
            reasoning=reasoning,
            viable_routes=viable_routes,
            stats_by_name=stats,
            picked_tier=tier,
        )
        return (agent, model, reasoning, assignment_reason)

    async def _route_task_config(
        self,
        required_caps: List[str],
        complexity: str,
        suggested: Optional[str],
        quota: Optional[Dict[str, Dict[str, float]]],
        suggested_model: Optional[str] = None,
    ) -> Tuple[str, Optional[str], str, Dict[str, Any]]:
        """Route using config-based model_routing (fallback when API unavailable).

        Returns ``(agent, model, routing_reasoning, assignment_reason)``.

        W10.4 follow-on: matches the API path's ``Phase 0`` guard — a
        planner-named agent that is not in ``self.config.agents`` (the
        active lineup this orchestrator was built with) raises
        ``SuggestedAgentDisabled`` instead of silently routing to a
        different agent. The config-only path doesn't have a settings
        URL handy (the API endpoint is what carries ``settings_path``);
        the error's message falls back to a generic agent-lineup hint.
        """
        # Phase 0: refuse silent fallback for a planner-suggested agent
        # that is NOT viable on this board. Two flag shapes, both
        # equivalent to "operator disabled this on board N":
        #   * absent from self.config.agents (the lineup) — the
        #     planner named an agent that was never enrolled here
        #   * present in self.config.agents but enabled=False — the
        #     planner's lineup was generated before the operator
        #     flipped the switch off (board 6's exact symptom)
        #
        # Both paths raise ``SuggestedAgentDisabled`` so the operator
        # sees the named agent + a hint pointing at the settings page.
        # Symmetric with the dispatch-side
        # ``tasks.dag_executor.AgentNotEnabledOnBoard`` and with
        # ``_route_task_api``'s Phase 0 guard — every layer surfaces
        # the same error so the operator sees one consistent line
        # whether the trigger is the planner, the dispatcher, or a
        # manual assign.
        if suggested and (
            suggested not in self.config.agents
            or not self.config.agents[suggested].enabled
        ):
            raise SuggestedAgentDisabled(
                agent=suggested,
                settings_url="",
            )

        # Phase 1: honour the suggested agent (and optionally model) if possible
        if suggested:
            # If planner suggested a specific model, try that route first.
            # No auto-upgrade — the planner already knows the complexity.
            if suggested_model:
                for route in self.config.model_routing:
                    if route.agent == suggested and route.model == suggested_model:
                        if self._route_viable(route, required_caps, complexity, quota):
                            cfg = self.config.agents.get(route.agent)
                            if cfg and await self._is_available_cached(route.agent, cfg):
                                reasoning = (
                                    f"Routed to {suggested}/{suggested_model} (suggested model)"
                                )
                                tier = cfg.cost_tier.value if cfg else "medium"
                                assignment_reason = self._build_assignment_reason(
                                    agent=suggested,
                                    model=suggested_model,
                                    rule="suggested-model",
                                    reasoning=reasoning,
                                    viable_routes=None,
                                    stats_by_name=None,
                                    picked_tier=tier,
                                )
                                return (suggested, suggested_model, reasoning, assignment_reason)

            result = await self._try_routes_for_agent(
                suggested, required_caps, complexity, quota
            )
            if result:
                agent, model = result
                model, reason_suffix = self._maybe_upgrade_model(agent, model, complexity)
                reasoning = (
                    f"Routed to {agent}/{model} (suggested by planner{reason_suffix})"
                )
                cfg = self.config.agents.get(agent)
                tier = cfg.cost_tier.value if cfg else "medium"
                assignment_reason = self._build_assignment_reason(
                    agent=agent,
                    model=model,
                    rule="suggested-agent",
                    reasoning=reasoning,
                    viable_routes=None,
                    stats_by_name=None,
                    picked_tier=tier,
                )
                return (agent, model, reasoning, assignment_reason)

        # Phase 2: collect viable routes, distribute within tier
        viable_routes: List[Tuple[str, Optional[str], str]] = []
        for route in self.config.model_routing:
            if not self._route_viable(route, required_caps, complexity, quota):
                continue
            cfg = self.config.agents.get(route.agent)
            if not await self._is_available_cached(route.agent, cfg):
                continue
            tier = cfg.cost_tier.value if cfg else "medium"
            viable_routes.append((route.agent, route.model, tier))

        if not viable_routes:
            tried = [f"{r.agent}/{r.model}" for r in self.config.model_routing]
            raise RuntimeError(
                f"No viable route for task (caps={required_caps}, "
                f"complexity={complexity}, suggested={suggested}). "
                f"Tried: {tried}"
            )

        tier_order = {CostTier.LOW.value: 0, CostTier.MEDIUM.value: 1, CostTier.HIGH.value: 2}

        # History-driven pick over the FULL viable pool so the
        # suggester can escalate across tiers when the cheapest tier
        # has no qualifier. Static fallback (thin history / no
        # qualifier) still distributes within the cheapest tier.
        # Defensive: any backend error → {} → fallback path. Default
        # First — never break plan over a stats fetch blip.
        try:
            stats = self._fetch_agent_stats()
        except Exception:
            stats = {}
        chosen, decision, route_suffix = self._pick_from_viable_routes(
            viable_routes, stats
        )
        agent, model, tier = chosen
        model, reason_suffix = self._maybe_upgrade_model(agent, model, complexity)

        # Surface the cheapest viable tier in the reasoning so the
        # operator can see whether the pick stayed in tier (cheap
        # path) or escalated (history path). When pick tier ≠ cheapest
        # tier, the reasoning explicitly says "escalated" so the
        # audit trail tells the truth.
        cheapest_tier = min(
            viable_routes, key=lambda r: tier_order.get(r[2], 1)
        )[2]
        cheap_tier_names = sorted(
            {r[0] for r in viable_routes if r[2] == cheapest_tier}
        )
        escalated = tier != cheapest_tier
        if escalated:
            pick_tier_names = sorted(
                {r[0] for r in viable_routes if r[2] == tier}
            )
            reasoning = (
                f"Routed to {agent}/{model} (escalated from "
                f"{cheapest_tier.upper()} tier to {tier.upper()} tier; "
                f"history-driven; cheapest-tier pool was "
                f"{', '.join(cheap_tier_names)}; pick-tier pool "
                f"{', '.join(pick_tier_names)}{route_suffix}{reason_suffix})"
            )
        else:
            reasoning = (
                f"Routed to {agent}/{model} ({tier.upper()} tier, "
                f"chosen from {len(cheap_tier_names)} viable "
                f"{tier.upper()}-tier route{'s' if len(cheap_tier_names) != 1 else ''}: "
                f"{', '.join(cheap_tier_names)}{route_suffix}{reason_suffix})"
            )

        # Same rule-detection logic as the API path.
        if isinstance(decision, RoutingDecision) and escalated:
            rule = "escalated"
        elif isinstance(decision, RoutingDecision):
            rule = "history"
        else:
            rule = "static-fallback"

        assignment_reason = self._build_assignment_reason(
            agent=agent,
            model=model,
            rule=rule,
            reasoning=reasoning,
            viable_routes=viable_routes,
            stats_by_name=stats,
            picked_tier=tier,
        )
        return (agent, model, reasoning, assignment_reason)

    def _over_quota(
        self, agent: str, quota: Optional[Dict[str, Dict[str, float]]], complexity: str
    ) -> bool:
        """Check if an agent is over quota threshold (except for high-complexity tasks)."""
        if not quota or agent not in quota:
            return False
        if complexity == "high":
            return False
        return quota[agent].get("usage_pct", 0) > self.config.quota_threshold

    def _maybe_upgrade_model_api(
        self, agent: str, model: Optional[str], complexity: str,
        agent_data: Dict[str, Any],
    ) -> Tuple[Optional[str], str]:
        """Upgrade to premium_model for high-complexity tasks using API data."""
        if complexity == "high":
            premium = agent_data.get("premium_model")
            if premium and premium != model:
                # Check the model is enabled
                enabled_models = [
                    m["name"] for m in agent_data.get("models", [])
                    if m.get("enabled", True)
                ]
                if premium in enabled_models:
                    return (premium, ", upgraded to premium for high complexity")
        return (model, "")

    def _maybe_upgrade_model(
        self, agent: str, model: Optional[str], complexity: str
    ) -> Tuple[Optional[str], str]:
        """Upgrade to premium_model for high-complexity tasks if available.

        Returns (effective_model, reasoning_suffix).
        """
        if complexity == "high":
            cfg = self.config.agents.get(agent)
            if cfg and cfg.premium_model and cfg.premium_model != model:
                if not self._is_banned(cfg.premium_model):
                    return (cfg.premium_model, ", upgraded to premium for high complexity")
        return (model, "")

    async def _try_routes_for_agent(
        self,
        agent_name: str,
        required_caps: List[str],
        complexity: str,
        quota: Optional[Dict[str, Dict[str, float]]],
    ) -> Optional[Tuple[str, Optional[str]]]:
        """Try to find a viable route for a specific agent (config-based fallback)."""
        for route in self.config.model_routing:
            if route.agent != agent_name:
                continue
            if not self._route_viable(route, required_caps, complexity, quota):
                continue
            cfg = self.config.agents.get(route.agent)
            if not await self._is_available_cached(route.agent, cfg):
                continue
            return (route.agent, route.model)
        return None

    def _route_viable(
        self,
        route,
        required_caps: List[str],
        complexity: str,
        quota: Optional[Dict[str, Dict[str, float]]],
    ) -> bool:
        """Check if a route passes all non-availability checks (config-based fallback)."""
        cfg = self.config.agents.get(route.agent)
        if not cfg or not cfg.enabled:
            return False
        normalized_caps = self._normalize_capabilities(route.agent, cfg.capabilities)
        if required_caps and not all(
            cap in normalized_caps for cap in required_caps
        ):
            return False
        if self._is_banned(route.model):
            return False
        if self._over_quota(route.agent, quota, complexity):
            return False
        return True

    def _is_banned(self, model: Optional[str]) -> bool:
        """Check if a model is on the global ban list.

        Uses substring matching so 'o4-mini' bans 'o4-mini' and
        'gemini-2.0' bans 'gemini-2.0-flash'.
        """
        if not model or not self.config.banned_models:
            return False
        model_lower = model.lower()
        return any(ban.lower() in model_lower for ban in self.config.banned_models)



    @staticmethod
    def _orientation_block(working_dir: Optional[str]) -> str:
        """Point the agent at the repo's own docs so it doesn't re-discover
        layout every run, and remind it to spend steps efficiently.

        Only references docs that actually exist under *working_dir* — never
        sends the agent to a file that isn't there. The efficiency guidance is
        always included (it is harness-agnostic and universally applicable).

        This is the single biggest lever against two observed wave-3 wastes:
        agents re-deriving repo structure every boot, and mechanical tasks
        spending 100+ single-tool-call steps (each a full model round-trip)
        that could have been batched.
        """
        orient_lines: list[str] = []
        if working_dir:
            root = Path(working_dir)
            if (root / "CLAUDE.md").is_file():
                orient_lines.append(
                    "- Read `CLAUDE.md` in the working directory first — it carries this "
                    "project's conventions, commands, and guardrails. Don't re-derive what it states."
                )
            if (root / "docs" / "breadcrumb_analysis" / "_INDEX.md").is_file():
                orient_lines.append(
                    "- Before tracing any cross-layer flow, check "
                    "`docs/breadcrumb_analysis/_INDEX.md` — it maps symptoms to end-to-end "
                    "traces and is cheaper and more accurate than re-discovering the same paths."
                )

        block = ""
        if orient_lines:
            block += "## Orientation (read before exploring)\n" + "\n".join(orient_lines) + "\n\n"
        block += (
            "## Work efficiently\n"
            "- Batch independent tool calls into a single message so they run in parallel — "
            "don't serialize reads or searches that don't depend on each other.\n"
            "- Don't re-read a file you've already read or re-run a search you've already run; "
            "keep prior results in context.\n"
            "- Prefer targeted search (grep/glob) over reading whole files or directories.\n\n"
        )
        return block

    @staticmethod
    def _wrap_prompt(
        prompt: str,
        working_dir: Optional[str] = None,
        mcp_task_id: Optional[str] = None,
        mcps: Optional[List[str]] = None,
        skip_proof: bool = False,
        orient: bool = True,
        advisor_enabled: bool = False,
        advisor_max_consults: int = advisor.DEFAULT_MAX_CONSULTS,
        project_notes: str = "",
    ) -> str:
        """Append working directory, MCP guidance, and structured status envelope to a task prompt.

        When *mcp_task_id* is provided the prompt includes a section explaining
        the available TaskIt MCP tools and how to use them.  The ODIN-STATUS
        envelope stays as the programmatic fallback — MCP comments are for
        human visibility on the task board.

        When *orient* is True (default) a short repo-orientation + efficiency
        block is prepended so the agent starts from the repo's own docs instead
        of re-discovering layout, and batches tool calls instead of serializing
        one-per-step model round-trips.

        When *advisor_enabled* is True the prompt gains an optional consult
        protocol: when genuinely stuck, write ONE question to
        ``.odin/advice_request.md`` and keep working; a host-side watcher
        answers it via a single strong-model call and writes
        ``.odin/advice.md``, capped at *advisor_max_consults* per run (trial:
        docs/wiki/agent-practices/the-advisor-strategy.md).
        """
        preamble = ""
        if working_dir:
            preamble = f"Working directory: {working_dir}\n\n"
            # The odin-agents guest image bakes the full python test toolchain
            # (pytest + project deps for BOTH the odin and taskit-backend suites)
            # into the system python3. Without this hint, confined agents see a
            # bare interpreter, assume deps are missing, and rebuild a venv +
            # pip-install on every run — minutes of pure per-run waste. Tell them
            # up front so they invoke the suites directly with zero setup.
            preamble += (
                "Environment: the python test toolchain (pytest, Django, and project "
                "dependencies) is already installed in the system python3. Run the odin "
                "suite with `python3 -m pytest` and the taskit-backend suite with "
                "`python3 manage.py test` directly — do NOT create a virtualenv or run "
                "pip install for these suites.\n\n"
            )
        if orient:
            preamble += Orchestrator._orientation_block(working_dir)

        mcp_section = ""
        if mcp_task_id:
            # Per-task proof path: namespacing by task id prevents two tasks
            # on one spec branch from colliding at merge (the old shared
            # `.proof/proof.md` was a single mutable path for inherently
            # per-task data).
            proof_rel_dir = f".proof/task-{mcp_task_id}"
            proof_rel_path = f"{proof_rel_dir}/proof.md"
            if skip_proof:
                proof_step = (
                    '4. **Proof**: Skip proof — proof collection is disabled for this board. '
                    'Proceed directly to the ODIN-STATUS block.'
                )
                proof_rules = (
                    'DO NOT post a separate "completed" status_update. '
                    'Proof is disabled — output the ODIN-STATUS block after your build passes.'
                )
            else:
                proof_step = (
                    '4. **Proof — two surface layers, complete on disk, brief on the board.**\n'
                    '\n'
                    '   On disk (canonical, complete, no cap):\n'
                    f'   a. Create `{proof_rel_path}` in your worktree and write the FULL proof:\n'
                    '      files changed, what each change does, the build/verify commands you\n'
                    '      ran and their results, the commit hash, and links/paths to any\n'
                    '      supporting artifacts (screenshots, raw suite logs, traces).\n'
                    '      This file is the round-tripped proof — write it complete; do not\n'
                    '      truncate to fit the comment.\n'
                    f'   b. Drop raw suite outputs beside it as `{proof_rel_dir}/<name>.txt` (e.g.\n'
                    f'      `{proof_rel_dir}/odin_tests.txt`, `{proof_rel_dir}/backend_tests.txt`,\n'
                    f'      `{proof_rel_dir}/verify_sh.txt`) — full output, no cap, no summarising.\n'
                    f'   c. Do NOT commit the `{proof_rel_dir}/` directory — it stays untracked (the harness auto-commit excludes `.proof/` via pathspec); the reviewer reads these files directly from the worktree on disk.\n'
                    '\n'
                    '   On the board (summary, not the file contents):\n'
                    '   d. Call `taskit_add_comment` with comment_type="proof". The comment\n'
                    '      body is a short summary + pointer: 1-2 line justification that\n'
                    '      proves each acceptance criterion is MET, plus the relative path\n'
                    f'      `{proof_rel_path}` and the commit hash of your work. Do NOT paste the full\n'
                    '      file contents into the comment — the reviewer reads the\n'
                    '      proof file directly from the worktree on disk.\n'
                    '   e. `file_paths` parameter still lists every file you created or\n'
                    '      modified (the worktree files, not the proof comment body).\n'
                    '   f. THIS IS THE COMPLETION SIGNAL — a task without proof is\n'
                    '      incomplete and will be marked failed.'
                )
                proof_rules = (
                    'DO NOT post a separate "completed" status_update. The proof comment IS your completion message.\n'
                    'DO NOT skip step 3. You must call taskit_add_comment with comment_type="proof" before outputting ODIN-STATUS.\n'
                    f'DO NOT truncate or summarize the contents of `{proof_rel_path}` to fit the comment — the file is the source of truth, the comment is a pointer.'
                )

            mcp_section = f"""

## TaskIt MCP Tools

You have access to TaskIt MCP tools for communicating with the task board.
Your task ID is: {mcp_task_id}

You MUST follow this exact sequence — no steps may be skipped:

1. **Start**: call `taskit_add_comment` with comment_type="status_update" — what you're about to do
2. **Do your work** (write code, create files, etc.)
3. **Build & verify**: run the project's build command (e.g. `npm run build`, `python -m py_compile`, `cargo build`) and confirm it succeeds with zero errors. If the build fails, fix the errors before proceeding. A task is NOT done until the build passes.
{proof_step}
5. Then output the ODIN-STATUS block below.

If you are blocked and need human input, call `taskit_add_comment` with comment_type="question" — this pauses until a human replies.

{proof_rules}

"""

        chrome_devtools_section = ""
        if mcps and "chrome-devtools" in mcps and not skip_proof:
            chrome_devtools_section = """
## Chrome DevTools MCP — Visual Proof

You have access to browser automation via chrome-devtools-mcp (page navigation, screenshots, DOM inspection, network monitoring, etc.).

**When your task creates or modifies anything that can be viewed in a browser**, you MUST capture a screenshot as part of your proof:

1. Open the page — use `navigate_page` with the appropriate URL:
   - Static HTML files: `file:///absolute/path/to/file.html`
   - Dev server / web app: `http://localhost:<port>/relevant/path`
   - Already-deployed page: the URL provided in the task description
2. Verify the page loaded: call `take_snapshot()` and confirm meaningful content is present (not a blank page or error screen).
3. Capture the screenshot: `take_screenshot(filePath="/tmp/proof_{task_id}.png")`
4. **Verify the file exists**: Run `ls -la /tmp/proof_{task_id}.png` and confirm it shows a non-zero file size. If the file is missing or empty, re-attempt the screenshot once. If it still fails, note this in your proof.
5. Include it in your proof: `taskit_add_comment(comment_type="proof", screenshot_paths=["/tmp/proof_{task_id}.png"], ...)`
6. **Check the result**: The tool returns a `screenshots_attached` count. If it is 0 despite providing paths, note the warning in a follow-up status_update.

**If the screenshot step fails** (browser not reachable, page errors), submit text-only proof with a note explaining what you tried and why it failed.

**Non-visual tasks** (pure logic, config, backend-only code with no UI) do NOT require screenshots — text-only proof is fine.

"""

        mobile_section = ""
        if mcps and "mobile" in mcps and not skip_proof:
            mobile_section = """
## Mobile MCP Tools

You have access to mobile device automation via mobile-mcp.

**CRITICAL — Do NOT start dev servers.** Never run `expo start`, `npm start`, `npm run dev`, `npx react-native start`, or any similar command. The human manages the dev server. If the app is not running or not responding on the device, ask the human via `taskit_add_comment(comment_type="question")`.

**Before interacting with a mobile device:**
1. Call `mobile_list_available_devices` to discover running emulators/simulators
2. If no devices found, ask the human via `taskit_add_comment(comment_type="question")`

**The app is already running on a device/emulator** — the human manages the dev server. Your code changes trigger hot-reload automatically.

**Proof sequence — ALL steps are MANDATORY. You must attempt every step in order.**
1. Do your work (write code, create files, etc.)
2. **Build gate — MANDATORY before any device interaction.** Run the project's build or typecheck command and confirm zero errors. Check `package.json` scripts, `tsconfig.json`, `Makefile`, or equivalent to find the right command (e.g. `npx tsc --noEmit`, `npm run build`, `python -m py_compile`).
   - If the build fails, **fix the errors and re-run until it passes**. Do NOT proceed to device steps with a broken build — the dev server will crash and screenshots will fail.
   - After the build passes, verify the dev server is responsive by checking the port it runs on (look at the project's dev script or running processes): `curl -sf http://localhost:<port> > /dev/null && echo "OK" || echo "DEV SERVER DOWN"`. If the server is down, wait 5 seconds and retry once. If still down, ask the human via `taskit_add_comment(comment_type="question")`.
3. **Runtime error check — MANDATORY after build gate passes.** Your code changes hot-reload automatically. Check for runtime crashes on the device:
   - **Android:** `adb logcat -d -s ReactNativeJS:E ReactNative:E | tail -50`
   - **iOS:** `xcrun simctl spawn booted log show --predicate 'messageType == error' --last 2m --style compact 2>/dev/null | grep -iE 'react|expo|fatal|exception'`
   - If errors appear (NullPointerException, missing exports, red screen crashes, module resolution failures), **fix them and re-check until the log is clean**. Do NOT proceed with a crashing app — screenshots of a crash screen are not proof of work.
   - Document each error you found and fixed in your proof comment (step 8). This is valuable evidence.
4. Call `mobile_list_available_devices` to find a device. You MUST call this — do not skip to text-only proof.
5. Launch the app on the device. **Important — Expo/React Native apps run inside Expo Go (`host.exp.exponent`), NOT as standalone APKs.** Do NOT guess a package name. Instead: call `mobile_launch_app(device="<device_id>", packageName="host.exp.exponent")` to open Expo Go, then use `mobile_open_url(device="<device_id>", url="exp://localhost:8081")` to load the project.
6. **Navigate to the screen or flow YOU built in this task** using mobile tools (`mobile_click_on_screen_at_coordinates`, `mobile_swipe_on_screen`, `mobile_type_keys`, etc.). Your screenshot must show YOUR work — not the home screen or a screen built by a previous task. If your task added a Team Setup screen, navigate to Team Setup. If your task built the Round Play flow, navigate through the gate screen into a round. The screenshot is proof that your specific deliverable works on device.
7. Save screenshot: `mobile_save_screenshot(device="<device_id>", saveTo="/tmp/proof_{task_id}.png")`. Take multiple screenshots if your task delivers a multi-step flow (e.g., gate → play → round end). Name them `/tmp/proof_{task_id}_1.png`, `/tmp/proof_{task_id}_2.png`, etc.
8. Submit proof with screenshot: `taskit_add_comment(comment_type="proof", file_paths=[...], screenshot_paths=["/tmp/proof_{task_id}.png"])`
   - Include in the proof summary: files changed, build result, runtime errors found and fixed (if any), and **what each screenshot shows and why it proves your task is complete**.

**If any step 4-7 fails** (no device, app won't load, screenshot is blank/loading), you MUST still submit proof in step 8 — but as text-only:
`taskit_add_comment(comment_type="proof", file_paths=[...])` with a note explaining: "Screenshot unavailable — [what you tried and why it failed]. Verify manually on device."

**RULES:**
- You MUST pass the build gate (step 2) AND runtime error check (step 3) before ANY device interaction. A broken build or crashing app = wasted screenshots.
- If you find and fix runtime errors during step 3, that is part of your work — document what was broken and how you fixed it in proof.
- You MUST attempt mobile verification (steps 4-7). Skipping straight to text-only proof is NOT allowed.
- You MUST submit proof (step 8) no matter what. A task without proof WILL be marked failed.
- Text-only proof is acceptable ONLY after a genuine attempt at screenshot capture failed.
- NEVER exit without calling `taskit_add_comment(comment_type="proof")`. No exception.
- NEVER guess package names for Expo/React Native apps. Always use `host.exp.exponent` + `mobile_open_url`.

"""

        # Pre-completion self-audit gate. Born from the task-170 forensic
        # audit: an agent shipped triplicate definitions of one helper plus
        # ~90 lines of commented-out dead code, which escaped into review and
        # cost a full rework round. Moving the check LEFT of the SUCCESS
        # emission (rather than catching it in reflection) prevents the slop
        # from reaching review at all. Rides on every prompt, like the
        # ODIN-STATUS envelope it guards — every SUCCESS emission deserves the
        # same hygiene check.
        self_audit_section = """
## Pre-completion self-audit gate (run BEFORE ODIN-STATUS)

Two cheap defect classes escape into review and force rework rounds. Verify
both before you emit ODIN-STATUS — a 30-second grep now beats a full review
cycle.

1. **No duplicate definitions.** For every function/class/symbol name you
   added, confirm it is defined exactly once in its file (and not duplicated
   repo-wide). Two `def foo(` lines at module scope silently shadow each other
   — the second wins, the first becomes dead code.
2. **No commented-out dead code.** Delete disabled `/* ... */` / `# ...` logic
   blocks, or replace them with a real comment explaining why they stay. Don't
   ship commented-out code — it rots and misleads the next reader.

Mechanical assist: run `scripts/self_audit_diff.sh` (no args audits your
working-tree diff; pass a commit ref to audit a specific change). It flags
duplicate definitions and commented-out code blocks in the files you touched.
If it prints anything, fix it before SUCCESS.

"""

        # Advisor trial (routing-and-cost bucket): a capped strong-model
        # consult for genuinely stuck moments, not a general-purpose help
        # button — define "stuck" narrowly so the cap (default 2/run) is
        # spent on real gates, not routine questions.
        advisor_section = ""
        if advisor_enabled:
            advisor_section = f"""
## Advisor consult (optional, capped at {advisor_max_consults}/run)

If you get genuinely stuck — the SAME test fails twice in a row, or you
face an architecture choice you hold real, stated uncertainty about — you
may consult a stronger model instead of grinding on it or guessing:

1. Write ONE clear paragraph describing the question and what you already
   tried to `{advisor.REQUEST_RELPATH}` in your worktree.
2. Keep working on other parts of the task if there is any other part
   left to make progress on — do NOT block on the answer.
3. Poll for `{advisor.ANSWER_RELPATH}` (e.g. check every ~30s while doing
   other work). When it appears, read it and continue.
4. This is capped at {advisor_max_consults} consults for the whole run —
   a request beyond the cap gets a refusal written back immediately, so do
   not wait indefinitely; if you see the cap-reached refusal, proceed with
   your own best judgment.

Do not use this for routine questions you can answer yourself by reading
the repo. It is for genuine, stated uncertainty only.

"""

        # Durable per-project notes (PROJECT_NOTES.md), injected after the
        # brief so every task starts from the same hard-won context instead
        # of re-deriving it. Content is pre-capped by the caller
        # (read_project_notes); here we only label and position it.
        notes_section = ""
        if project_notes:
            notes_section = (
                "\n## Project Notes (durable context for this project)\n\n"
                f"{project_notes}\n\n"
            )

        return f"""{preamble}{prompt}{notes_section}{mcp_section}{chrome_devtools_section}{mobile_section}{self_audit_section}{advisor_section}
IMPORTANT — After completing your work, you MUST end your response with a status
block in exactly this format (including the separator lines):

-------ODIN-STATUS-------
SUCCESS or FAILED
-------ODIN-SUMMARY-------
<1-2 sentence summary of what was accomplished or what went wrong>"""

    @staticmethod
    def _compose_comment(verb: str, result: TaskResult, summary_text: str) -> str:
        """Compose a metrics-inline comment from a TaskResult.

        Output format:
          "Completed in 12.3s · 8,420 tokens (5,200 in / 3,220 out)\\n\\nSummary text"
        """
        metrics_parts: list[str] = []
        if result.duration_ms:
            metrics_parts.append(f"{result.duration_ms / 1000:.1f}s")
        usage = result.metadata.get("usage", {})
        total = usage.get("total_tokens")
        if total:
            input_t = usage.get("input_tokens") or usage.get("prompt_tokens")
            output_t = usage.get("output_tokens") or usage.get("completion_tokens")
            if input_t and output_t:
                metrics_parts.append(
                    f"{total:,} tokens ({input_t:,} in / {output_t:,} out)"
                )
            else:
                metrics_parts.append(f"{total:,} tokens")

        if metrics_parts:
            metrics_line = f"{verb} in " + " · ".join(metrics_parts)
            return f"{metrics_line}\n\n{summary_text}"
        return summary_text

    @staticmethod
    def _classify_failure(exc: Exception, phase: str) -> Dict[str, str]:
        """Classify execution exceptions into a stable failure taxonomy."""
        name = type(exc).__name__.lower()
        message = str(exc).lower()
        text = f"{name} {message}"

        # Task #331: a sandbox/runtime that never started an agent is a
        # pre-execution (infra) failure — no agent ran, so no agent is
        # blamed and the routing policy retries the infra failure.
        if any(sig in text for sig in Orchestrator._SANDBOX_UNAVAILABLE_SIGNATURES):
            failure_type = "sandbox_unavailable"
        elif "timeout" in text:
            failure_type = "timeout"
        elif (
            "model" in text
            and any(tok in text for tok in ["not supported", "unsupported", "invalid_request_error"])
        ):
            failure_type = "model_escalation_failure"
        elif any(tok in text for tok in ["http", "api", "rate", "quota", "token", "429"]):
            failure_type = "llm_call_failure"
        elif any(tok in text for tok in ["subprocess", "exec", "command", "exit", "tmux"]):
            failure_type = "agent_execution_failure"
        elif any(tok in text for tok in ["backend", "taskit", "httpstatuserror", "connection"]):
            failure_type = "backend_exception"
        else:
            failure_type = "internal_error"

        return {
            "failure_type": failure_type,
            "failure_reason": f"{type(exc).__name__}: {exc}"[:500],
            "failure_origin": f"orchestrator:{phase}",
            "failure_phase": phase,
        }

    @staticmethod
    def _sanitize_trace_excerpt(text: str, limit: int = 1200) -> str:
        """Return a compact, sanitized tail excerpt for failure debugging."""
        if not text:
            return ""
        lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
        excerpt = "\n".join(lines[-20:])
        return excerpt[:limit]

    @staticmethod
    def _extract_agent_text(raw_output: str) -> str:
        """Extract human-readable agent text from structured CLI output.

        Agent CLIs (Claude Code, Gemini, etc.) stream structured JSON where the
        agent's text response is embedded inside JSON string values.  The
        ODIN-STATUS envelope lives inside those values, so plain-text search
        on the raw output crosses JSON boundaries and produces broken content.

        This method detects the output format and extracts just the agent's
        text content:

        - **Claude Code JSONL**: ``{"type":"text","part":{"text":"..."}}``
        - **Gemini stream-json**: ``{"type":"text","text":"..."}``
        - **Claude stream-json deltas**: ``{"type":"content_block_delta","delta":{"text":"..."}}``
        - **Claude/Gemini result**: ``{"type":"result","result":"..."}``
        - **Plain text**: returned as-is (mock harness, direct execution).
        """
        if not raw_output or not raw_output.strip():
            return raw_output

        lines = raw_output.strip().splitlines()

        text_parts: list[str] = []
        json_line_count = 0

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if not stripped.startswith("{"):
                continue
            try:
                obj = json.loads(stripped)
            except (json.JSONDecodeError, TypeError):
                continue

            if not isinstance(obj, dict):
                continue

            json_line_count += 1
            event_type = obj.get("type")

            # Claude Code JSONL: {"type":"text","part":{"text":"..."}}
            if event_type == "text":
                part = obj.get("part", {})
                text = part.get("text", "")
                if text:
                    text_parts.append(text)
                    continue
                # Gemini stream-json: {"type":"text","text":"..."}
                text = obj.get("text", "")
                if text:
                    text_parts.append(text)
                    continue

            # Claude stream-json: {"type":"content_block_delta","delta":{"text":"..."}}
            if event_type == "content_block_delta":
                delta = obj.get("delta", {})
                text = delta.get("text", "")
                if text:
                    text_parts.append(text)
                    continue

            # Claude/Gemini stream-json: {"type":"result","result":"..."}
            if event_type == "result":
                result_text = obj.get("result", "")
                if isinstance(result_text, str) and result_text:
                    text_parts.append(result_text)
                    continue

            # Codex CLI: {"type":"item.completed","item":{"type":"agent_message","text":"..."}}
            if event_type == "item.completed":
                item = obj.get("item", {})
                if isinstance(item, dict) and item.get("type") == "agent_message":
                    text = item.get("text", "")
                    if text:
                        text_parts.append(text)
                continue

        if json_line_count > 0 and text_parts:
            return "\n".join(text_parts)

        # No recognized structured events — return raw output
        return raw_output

    @staticmethod
    def _parse_envelope(output: str) -> Tuple[str, Optional[bool], Optional[str]]:
        """Parse the ODIN-STATUS envelope from agent output.

        Returns (clean_output, parsed_success, summary).
        If the envelope is not found, returns (output, None, None).
        """
        separator = "-------ODIN-STATUS-------"
        summary_separator = "-------ODIN-SUMMARY-------"

        idx = output.rfind(separator)
        if idx == -1:
            return (output, None, None)

        clean_output = output[:idx].rstrip()
        tail = output[idx + len(separator):]

        # Extract status line and summary
        summary_idx = tail.find(summary_separator)
        if summary_idx != -1:
            status_text = tail[:summary_idx].strip().upper()
            summary = tail[summary_idx + len(summary_separator):].strip()
        else:
            status_text = tail.strip().upper()
            summary = None

        parsed_success = None
        if "SUCCESS" in status_text:
            parsed_success = True
        elif "FAILED" in status_text or "FAIL" in status_text:
            parsed_success = False

        return (clean_output, parsed_success, summary)

    # Substrings in a harness error that mean the sandbox/runtime never
    # started an agent — a pre-execution failure, not an agent failure.
    _SANDBOX_UNAVAILABLE_SIGNATURES = (
        "microsandbox",
        "no cli command to run in microsandbox",
        "sandbox unavailable",
        "sandbox is not available",
        "could not start sandbox",
        "sandbox failed to boot",
        "msb: command not found",
        "msb daemon",
    )

    # The generic "no ODIN-STATUS / no output" complaint each harness emits
    # when it can't find the marker. This IS the fragile stdout-tail
    # verdict task #331 replaces — it is speculation ("Likely the model
    # truncated..."), not a true reason, so the ladder discards it and
    # relies on the worktree evidence + its own factual defaults instead.
    _GENERIC_MARKER_ABSENCE_SIGNATURES = (
        "did not emit an odin-status",
        "did not emit odin-status",
        "produced no output",
        "terminated silently before emitting",
    )

    @classmethod
    def _classify_exit_condition(
        cls, raw_result: "TaskResult"
    ) -> Tuple[str, Optional[str]]:
        """Map a harness result onto an evidence-ladder exit condition +
        the true reason string to attach on failure.

        Returns ``(exit_condition, reason_detail)``. ``reason_detail`` is
        the real error text from the stream/exit and is preserved for
        every concrete failure (timeout, nonzero, sandbox, a specific
        provider error). It is dropped ONLY for the harness's generic
        marker-absence complaint — the speculative stdout-tail verdict
        task #331 replaces — so the ladder uses the worktree evidence and
        its own factual defaults instead.
        """
        error = (raw_result.error or "")
        error_lower = error.lower()
        if any(sig in error_lower for sig in cls._SANDBOX_UNAVAILABLE_SIGNATURES):
            return "sandbox_unavailable", error or None
        if "timed out" in error_lower or "timeout" in error_lower:
            return "timeout", error or None
        if "exited with code" in error_lower:
            return "nonzero", error or None
        if any(sig in error_lower for sig in cls._GENERIC_MARKER_ABSENCE_SIGNATURES):
            # Speculative marker-absence text — discard, treat as a clean
            # exit and let the worktree evidence / factual default decide.
            return "clean", None
        if error.strip():
            # A concrete, harness-reported error (e.g. "model not
            # supported"). That text IS the true reason from the stream —
            # keep it, and treat it as an agent-level failure so it is not
            # mislabeled a silent hang.
            return "nonzero", error
        # Genuinely clean exit with no error at all.
        return "clean", None

    def _read_status_file_signal(self, working_dir: Optional[str]) -> Optional[bool]:
        """Read an explicit verdict from a status file the agent may drop
        in the worktree (``.odin/status`` — ``SUCCESS`` / ``FAILED``).

        This is a second explicit-signal channel alongside the stdout
        block: an agent that finished the work but never printed the
        marker can still confirm success out-of-band. Returns None when no
        readable file/verdict exists. Never raises — reading it is
        bookkeeping around the run.
        """
        if not working_dir:
            return None
        try:
            status_path = Path(working_dir) / ".odin" / "status"
            if not status_path.is_file():
                return None
            token = status_path.read_text(errors="replace").strip().upper()
        except OSError:
            return None
        if not token:
            return None
        first = token.split()[0]
        if first == "SUCCESS":
            return True
        if first in ("FAILED", "FAIL"):
            return False
        return None

    def _count_task_comments(self, task_id: str) -> int:
        """How many comments the task already carries. Captured at run
        start so the MCP signal reader can ignore proof comments a prior
        attempt left on the same task (the comment analogue of the start
        HEAD). Best-effort: never raises — it is bookkeeping around the run
        (see docs/patterns/bookkeeping-never-kills-the-run)."""
        try:
            return len(self.task_mgr.get_comments(task_id) or [])
        except Exception:
            return 0

    def _read_mcp_signal(
        self, task_id: str, *, baseline: int = 0
    ) -> Optional[bool]:
        """Read an explicit completion signal from the task board — the MCP
        channel.

        An agent that finishes through the TaskIt MCP tools posts a
        ``proof`` comment; that comment IS its completion message ("The
        proof comment IS your completion message"). So a ``proof`` comment
        added during this run is an explicit SUCCESS verdict — the third
        explicit-signal channel alongside the stdout block and the status
        file. An agent whose stdout tail was truncated but who confirmed
        done out-of-band via MCP is believed, not failed.

        ``baseline`` is the task's comment count captured at run start;
        only comments beyond it belong to this run. This scopes the signal
        to the current attempt, so a ``proof`` comment a prior attempt left
        on the same task cannot masquerade as this run's verdict — the same
        run-scoping the worktree-delta channel gets from the start HEAD.

        Returns True when this run posted a proof comment, else None (no
        MCP verdict — fall through to the worktree evidence). Never raises:
        reading the board is bookkeeping around the run and must not crash
        it.
        """
        try:
            comments = self.task_mgr.get_comments(task_id) or []
        except Exception:
            return None
        for comment in comments[baseline:]:
            if isinstance(comment, dict):
                ctype = comment.get("comment_type")
            else:
                ctype = getattr(comment, "comment_type", None)
            if ctype == "proof":
                return True
        return None

    def _read_run_stream_summary(
        self, trace_file: str, output_file: str, raw_result: "TaskResult"
    ) -> Dict[str, Any]:
        """Extract run-close stream metadata from the trace, once per run.

        Prefers the raw JSONL trace, falls back to the extracted-text output
        file, then the in-memory result. Works in mock mode too (it only
        reads files). Never raises — a parse hiccup yields an empty summary
        and the ladder falls back to its worktree evidence."""
        raw = ""
        try:
            trace_path = Path(trace_file)
            output_path = Path(output_file)
            if trace_path.exists() and trace_path.stat().st_size > 0:
                raw = trace_path.read_text(errors="replace")
            elif output_path.exists() and output_path.stat().st_size > 0:
                raw = output_path.read_text(errors="replace")
            else:
                raw = raw_result.output or raw_result.error or ""
        except OSError:
            raw = raw_result.output or raw_result.error or ""
        try:
            return extract_stream_summary(raw)
        except Exception:
            self._log.debug(
                "stream_summary extraction failed; using empty summary",
                exc_info=True,
            )
            return {}

    @staticmethod
    def _is_isolated_worktree(working_dir: Optional[str]) -> bool:
        """True when ``working_dir`` is an isolated task worktree (a path
        under ``.odin/worktrees``). The truncation-resume path only trusts
        the isolated-worktree work signal there — never the shared project
        checkout, where uncommitted dirt could be a developer's, not the
        agent's."""
        if not working_dir:
            return False
        parts = Path(working_dir).parts
        for i in range(len(parts) - 1):
            if parts[i] == ".odin" and parts[i + 1] == "worktrees":
                return True
        return False

    def _read_prev_trace_tail(
        self, task_id: str, *, max_chars: int = 1500
    ) -> str:
        """Return the tail of the previous attempt's trace (task #332/#330).

        Traces live host-side (``_resolve_host_log_dir``) and survive worktree
        resets between attempts. At resume-prompt build time the new attempt
        has not rotated yet, so the canonical ``task_<id>.trace.jsonl`` still
        holds the PRIOR attempt's stream; the ``attempt-N`` archives are the
        fallback. The tail is extracted to plain text so a resuming agent sees
        what the last attempt was doing when it got cut off. Never raises."""
        try:
            log_dir = self._resolve_host_log_dir(self.config.log_dir)
        except Exception:
            return ""
        canonical = log_dir / f"task_{task_id}.trace.jsonl"
        src: Optional[Path] = None
        if canonical.exists() and canonical.stat().st_size > 0:
            src = canonical
        else:
            def _attempt_index(p: Path) -> int:
                stem = p.name
                marker = ".attempt-"
                if marker in stem:
                    try:
                        return int(stem.split(marker, 1)[1].split(".", 1)[0])
                    except ValueError:
                        return 0
                return 0

            archives = sorted(
                log_dir.glob(f"task_{task_id}.trace.attempt-*.jsonl"),
                key=_attempt_index,
            )
            if archives:
                src = archives[-1]
        if src is None:
            return ""
        try:
            raw = src.read_text(errors="replace")
        except OSError:
            return ""
        text = extract_text_from_stream(raw) or raw
        return text[-max_chars:].strip()

    def _build_resume_prompt(
        self, task_id: str, working_dir: Optional[str], task: "Task"
    ) -> str:
        """Assemble the host-side resume prompt for a truncated attempt.

        Gathers the two reconstructed pieces — a git status/diff summary of
        the worktree and the tail of the previous attempt's trace — and hands
        them to :func:`build_resume_prompt`. Returns "" when there is nothing
        to resume from (no worktree state and no trace), so a fresh start
        never gets a misleading resume header."""
        resume_count = int((task.metadata or {}).get("truncation_resume_count", 0)) or 1
        worktree_state = summarize_worktree_state(working_dir)
        trace_tail = self._read_prev_trace_tail(task_id)
        return build_resume_prompt(
            resume_count=resume_count,
            worktree_state=worktree_state,
            trace_tail=trace_tail,
        )

    def _decide_task_outcome(
        self,
        *,
        raw_result: "TaskResult",
        parsed_success: Optional[bool],
        working_dir: Optional[str],
        start_head: Optional[str],
        start_dirty: bool = False,
        task_id: Optional[str] = None,
        comment_baseline: int = 0,
        stream_summary: Optional[Dict[str, Any]] = None,
    ) -> RunOutcome:
        """Apply the evidence ladder (task #331 + #332) to a finished run.

        Gathers the explicit-verdict channels in cheapest-first order —
        the stdout block, then a status file on disk, then the MCP proof
        comment on the board — plus the worktree delta vs the recorded
        start HEAD and the exit condition, and delegates the verdict to
        :func:`decide_run_outcome`. Any one explicit verdict is believed;
        the board is only consulted when the two local channels are silent.

        Task #332: when the provider stream ended at an output-cap finish
        reason and there is work in the (isolated) worktree, the verdict is
        ``truncated_resumable`` — the executor requeues the same task into the
        same worktree with a host-built resume prompt instead of reviewing a
        half-finished diff.
        """
        explicit_signal = parsed_success
        signal_source = "stdout" if parsed_success is not None else None
        if explicit_signal is None:
            file_signal = self._read_status_file_signal(working_dir)
            if file_signal is not None:
                explicit_signal = file_signal
                signal_source = "status_file"
        if explicit_signal is None and task_id is not None:
            mcp_signal = self._read_mcp_signal(task_id, baseline=comment_baseline)
            if mcp_signal is not None:
                explicit_signal = mcp_signal
                signal_source = "mcp"

        exit_condition, reason_detail = self._classify_exit_condition(raw_result)
        has_work = worktree_has_work_since(
            working_dir, start_head, start_dirty=start_dirty
        )

        # Task #332: did the provider hit its output cap? The resume decision
        # only fires when the run was cut off mid-generation (not on a normal
        # stop). Inside an isolated worktree the accumulated prior-attempt
        # edits count as resumable work even when the tree was dirty at start,
        # so a second resume doesn't stall.
        truncated = finish_reason_is_output_cap(
            (stream_summary or {}).get("finish_reason")
        )
        has_resumable_work: Optional[bool] = None
        if truncated:
            if self._is_isolated_worktree(working_dir):
                has_resumable_work = worktree_has_resumable_work(working_dir, start_head)
            else:
                has_resumable_work = has_work

        return decide_run_outcome(
            explicit_signal=explicit_signal,
            signal_source=signal_source,
            has_work=has_work,
            exit_condition=exit_condition,
            reason_detail=reason_detail,
            truncated=truncated,
            has_resumable_work=has_resumable_work,
        )

    def mark_interrupted(self) -> None:
        """Mark all IN_PROGRESS/EXECUTING tasks as FAILED with 'Stopped by user' comment.

        Called when the executor receives SIGTERM or exits unexpectedly.
        """
        all_tasks = self.task_mgr.list_tasks(status=TaskStatus.IN_PROGRESS)
        all_tasks += self.task_mgr.list_tasks(status=TaskStatus.EXECUTING)
        for task in all_tasks:
            self.task_mgr.update_status(task.id, TaskStatus.FAILED)
            self.task_mgr.add_comment(task.id, "odin", "Stopped by user")
            self.logger.log(
                action="task_interrupted",
                task_id=task.id,
                agent=task.assigned_agent or "unknown",
            )

    async def _execute_via_tmux(
        self,
        task_id: str,
        cmd: List[str],
        working_dir: str,
        output_file: str,
        agent_name: str,
        timeout_seconds: Optional[int] = None,
        env_vars: Optional[Dict[str, str]] = None,
    ) -> TaskResult:
        """Run a CLI command inside a tmux session and return a TaskResult."""
        start = time.monotonic()

        # Store tmux session name in task metadata
        task_obj = self.task_mgr.get_task(task_id)
        if task_obj:
            task_obj.metadata["tmux_session"] = tmux.session_name(task_id)
            self.task_mgr.update_task(task_obj)

        await tmux.launch(cmd, working_dir, task_id, output_file, env_vars=env_vars)
        timeout = timeout_seconds if timeout_seconds and timeout_seconds > 0 else None
        exit_code = await tmux.wait_for_exit(
            task_id, output_file, timeout=timeout,
            completion_checker=stream_json_is_complete,
        )

        duration = (time.monotonic() - start) * 1000

        # Read output from the log file
        output_path = Path(output_file)
        stdout_text = ""
        if output_path.exists():
            stdout_text = output_path.read_text()

        # Extract token usage regardless of which harness produced the output —
        # this is the tmux path, which bypasses each harness's own execute()
        # (and any usage extraction it does), so it must extract usage itself
        # or that data never reaches TaskResult.metadata.
        usage = extract_token_usage(stdout_text) if stdout_text else {}
        meta = {"usage": usage} if usage else {}

        if exit_code == 0:
            status_obj = validate_odin_status_full(
                stdout_text, worktree_path=working_dir,
            )
            agent_success, agent_error = status_obj.as_legacy_tuple()
            if status_obj.raw_block is not None:
                meta["malformed_status"] = {
                    "raw_block": status_obj.raw_block,
                    "inferred": status_obj.inferred,
                    "inference_reason": status_obj.inference_reason,
                }
            return TaskResult(
                success=agent_success,
                output=stdout_text,
                error=agent_error,
                duration_ms=round(duration, 1),
                agent=agent_name,
                metadata=meta,
            )
        elif exit_code == -1:
            timeout_msg = (
                f"Command timed out after {timeout_seconds}s"
                if timeout_seconds and timeout_seconds > 0
                else "Command timed out"
            )
            return TaskResult(
                success=False,
                output=stdout_text,
                error=timeout_msg,
                duration_ms=round(duration, 1),
                agent=agent_name,
                metadata=meta,
            )
        else:
            # Build a useful error message: exit code + last lines of output + log path
            error_parts = [f"Process exited with code {exit_code}"]
            if stdout_text.strip():
                tail_lines = stdout_text.strip().splitlines()[-20:]
                error_parts.append("Last output:\n" + "\n".join(tail_lines))
            if output_path.exists():
                error_parts.append(f"Full log: {output_file}")
            return TaskResult(
                success=False,
                output=stdout_text,
                error="\n\n".join(error_parts),
                duration_ms=round(duration, 1),
                agent=agent_name,
                metadata=meta,
            )


    @staticmethod
    def _reset_live_trace_files(trace_file: str, output_file: str) -> None:
        """Replace per-task live trace files at execution start.

        Deprecated: prefer ``_rotate_live_trace_files`` (F45 mandate #4).
        Kept as a thin wrapper for callers that have not migrated — it
        preserves prior behavior (unlink + touch) so the rewrite can roll
        out one orchestrator path at a time. New code MUST call the rotate
        variant so each attempt's evidence survives.
        """
        for raw in (trace_file, output_file):
            path = Path(raw)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            path.touch()

    @staticmethod
    def _resolve_host_log_dir(log_dir: str, *, cwd: Optional[Path] = None) -> Path:
        """Resolve a log_dir to the host project root, escaping worktrees.

        A relative ``log_dir`` resolves against the process cwd. When odin
        runs inside a task worktree (cwd under ``.odin/worktrees/...``) that
        lands the traces *inside the worktree*, where worktree lifecycle
        (reset/cleanup between attempts) destroys them — so the next attempt's
        rotation finds nothing to preserve (the task #314 loss). Rewriting to
        the host project root (the directory that *contains*
        ``.odin/worktrees``) makes traces survive across attempts in a stable,
        centrally-findable location.
        """
        base = Path(cwd) if cwd is not None else Path.cwd()
        rel = Path(log_dir)
        resolved = rel.resolve() if rel.is_absolute() else (base / rel).resolve()
        parts = resolved.parts
        for i in range(len(parts) - 1):
            if parts[i] == ".odin" and parts[i + 1] == "worktrees":
                host_root = Path(*parts[:i])
                if not rel.is_absolute():
                    return (host_root / rel).resolve()
                return resolved
        return resolved

    @staticmethod
    def _rotate_live_trace_files(trace_file: str, output_file: str) -> None:
        """Rotate per-task trace and .out files per attempt (F45 mandate #4).

        On every dispatch, the prior attempt's trace is preserved as an
        ``attempt-N`` archive sibling:

          - ``task_X.trace.jsonl``   → ``task_X.trace.attempt-N.jsonl``
          - ``task_X.out``           → ``task_X.out.attempt-N``

        N is the next available index, scanning existing siblings. Picking
        the first empty slot would clobber stale archives left by an
        interrupted prior rotation; scanning avoids that.

        The canonical paths are always recreated empty so the new attempt
        writes to a fresh inode (the UI WebSocket tailer still treats inode
        changes as a rerun reset, so reusing the same file would show stale
        chunks from the prior attempt until the new sandbox writes enough
        data).
        """
        for canonical in (trace_file, output_file):
            canonical_path = Path(canonical)
            canonical_path.parent.mkdir(parents=True, exist_ok=True)

            if not canonical_path.exists():
                canonical_path.touch()
                continue

            # Find the next attempt index by scanning existing siblings.
            name = canonical_path.name
            if name.endswith(".jsonl"):
                base, _, _tail = name.rpartition(".jsonl")
                archive_name_factory = (
                    lambda i: f"{base}.attempt-{i}.jsonl"
                )
            else:
                archive_name_factory = (
                    lambda i: f"{name}.attempt-{i}"
                )

            idx = 1
            while True:
                archive = canonical_path.with_name(
                    archive_name_factory(idx)
                )
                if not archive.exists():
                    break
                idx += 1

            canonical_path.rename(archive)
            canonical_path.touch()

    async def _execute_task(
        self,
        task_id: str,
        agent_name: str,
        prompt: str,
        working_dir: str,
        sem: asyncio.Semaphore,
        mock: bool = False,
    ) -> Dict[str, Any]:
        """Execute a single sub-task with an agent.

        Prefers tmux-based execution for CLI harnesses (runs in a named
        ``odin-<id>`` session users can attach to).  Falls back to the
        harness's own ``execute()`` when tmux is unavailable or the
        harness doesn't support ``build_execute_command()``.

        When mock=True, all backend writes (status updates, comments, cost
        tracking, metadata writes) are skipped. The harness still executes
        and results are returned.
        """
        async with sem:
            task_run_token = os.getenv("ODIN_TASK_RUN_TOKEN", "").strip()
            task_obj = self.task_mgr.get_task(task_id)
            # Resolve model: prefer task.metadata["selected_model"] (set at
            # plan time or via the TaskIt model_name field); when absent,
            # fall back to the assignee agent's default_model from the
            # TaskIt routing-config lineup (and then the agent config).
            model = self._resolve_task_model(task_obj, agent_name)

            # F45 mandate #2: record any model switch vs. the task's
            # authoritative values BEFORE harness execution so the audit
            # trail captures the change with reason + policy. Without this,
            # the silent model swap that bit task #114 (minimax/M3 →
            # gemini-3-flash-preview) repeats invisibly.
            if not mock and task_obj and model:
                self._record_model_pickup(
                    task=task_obj,
                    dispatch_model=model,
                    agent_name=agent_name,
                    reason="dispatch_resolved_model",
                    policy="f45_no_silent_model_switch",
                    actor="odin",
                    changed_by="odin",
                )
                # Persist the metadata key the helper stamped; the helper
                # already mutated task.metadata in-place, but if the
                # backend doesn't auto-refresh from there, we save here.
                if "last_model_change" in (task_obj.metadata or {}):
                    try:
                        self.task_mgr.update_task(task_obj)
                    except Exception:
                        self._log.debug(
                            "[task:%s] update_task after pickup failed; "
                            "history row + comment are still authoritative",
                            task_id,
                            exc_info=True,
                        )

            # Transition to EXECUTING unless already there (Celery path) or mock
            if not mock:
                if not task_obj or task_obj.status != TaskStatus.EXECUTING:
                    self.task_mgr.update_status(task_id, TaskStatus.EXECUTING)

                # Store started_at timestamp in task metadata
                task_obj = self.task_mgr.get_task(task_id)
                if task_obj:
                    task_obj.metadata["started_at"] = time.time()
                    self.task_mgr.update_task(task_obj)

            # Task #331: record the worktree's HEAD at run start — one hash
            # in run metadata. The evidence ladder measures work against
            # THIS run's start HEAD, so a prior attempt's commits on the
            # same branch cannot masquerade as this run's output. We also
            # record whether the tree was already dirty at start: a fresh
            # task worktree is clean, but a run that fell back to the shared
            # project checkout may sit on unrelated uncommitted edits that
            # must NOT be read as the agent's work.
            worktree_start_head = _git_head(working_dir)
            worktree_start_dirty = _git_has_uncommitted_changes(working_dir)
            # Comment count at run start — the baseline the MCP proof-signal
            # channel measures against, so a proof comment a prior attempt
            # left on this task is not read as this run's verdict.
            comment_baseline = self._count_task_comments(task_id)
            if not mock and task_obj is not None and worktree_start_head:
                try:
                    task_obj.metadata["worktree_start_head"] = worktree_start_head
                    self.task_mgr.update_task(task_obj)
                except Exception:
                    self._log.debug(
                        "[task:%s] could not record worktree_start_head", task_id,
                        exc_info=True,
                    )

            self._log.info(
                "[task:%s] Execution started: agent=%s, model=%s, mock=%s",
                task_id, agent_name,
                model or "-", mock,
            )
            self.logger.log(
                action="task_started", task_id=task_id, agent=agent_name
            )

            cfg = self.config.agents[agent_name]
            if not mock:
                self._assert_agent_cli_available(agent_name, action="execution")
            harness = get_harness(agent_name, cfg)

            # Compute output file path for live tailing.
            # Must be absolute — tmux sessions run with cwd=working_dir
            # (possibly a worktree), so relative paths would resolve to the
            # wrong location when the orchestrator reads them back.
            # Resolve host-side (escaping any worktree cwd) so traces survive
            # across retries — a worktree reset between attempts must not
            # destroy the prior attempt's evidence (task #314).
            log_dir = self._resolve_host_log_dir(self.config.log_dir)
            output_file = str(log_dir / f"task_{task_id}.out")
            trace_file = str(log_dir / f"task_{task_id}.trace.jsonl")
            # F45 mandate #4: rotate the prior attempt's trace into an
            # ``attempt-N.jsonl`` archive before starting so the new attempt
            # writes to a fresh canonical. Evidence from previous attempts
            # survives for post-mortem inspection.
            self._rotate_live_trace_files(trace_file, output_file)

            # Record the absolute trace path at execution START so live viewers
            # (TaskIt SessionConsumer) tail the right file regardless of which
            # cwd odin was launched with — celery runs use the task worktree,
            # so the board-root guess misses (post-run recording is too late).
            if not mock and task_obj is not None:
                try:
                    task_obj.metadata["trace_file"] = trace_file
                    task_obj.metadata["output_file"] = output_file
                    self.task_mgr.update_task(task_obj)
                except Exception:
                    self._log.debug(
                        "[task:%s] could not record trace_file metadata", task_id,
                        exc_info=True,
                    )

            context = {
                "working_dir": working_dir,
                "output_file": output_file,
                "trace_file": trace_file,
                "timeout_seconds": self.config.execution_timeout_seconds,
            }
            if model:
                context["model"] = model
            # Optional agentic step budget (Claude Code `--max-turns`). Bounds
            # runaway step counts on mechanical tasks; None leaves the CLI's
            # loop bound only by the wall-clock timeout (prior behavior).
            if self.config.max_turns:
                context["max_turns"] = self.config.max_turns

            # Generate MCP config for agent CLI integration. Keep always-on
            # TaskIt, but only attach heavy/browser/mobile MCPs when the current
            # task text asks for them. Otherwise small opencode tasks receive a
            # huge irrelevant prompt and frequently time out before ODIN-STATUS.
            task_text_for_mcps = "\n".join(
                str(x or "")
                for x in (
                    getattr(task_obj, "title", ""),
                    getattr(task_obj, "description", ""),
                    prompt,
                )
            )
            mcps = self._select_task_mcps(task_text_for_mcps)
            has_taskit = "taskit" in mcps and bool(self.config.taskit)
            has_mobile = "mobile" in mcps
            has_chrome_devtools = "chrome-devtools" in mcps
            mcp_available = has_taskit or has_mobile or has_chrome_devtools
            mcp_env_override = None
            if cfg.run_in_forkd and has_taskit:
                mcp_env_override = self._get_mcp_env(task_id, agent_name, model=model)
                # The concrete port is allocated by ForkdHarness at run time;
                # this placeholder is rewritten after the host proxy starts.
                mcp_env_override["TASKIT_URL"] = "__ODIN_FORKD_TASKIT_URL__"
                context["forkd_taskit_base_url"] = self.config.taskit.base_url
            elif cfg.sandbox_mode == "microsandbox" and has_taskit:
                # The guest cannot reach the host's 127.0.0.1 — generate configs
                # born guest-reachable (LAN IP) instead of rewriting placeholders.
                # taskit_base_url also tells the harness to open a host net rule.
                from odin.harnesses.microsandbox import guest_reachable_url

                mcp_env_override = self._get_mcp_env(task_id, agent_name, model=model)
                mcp_env_override["TASKIT_URL"] = guest_reachable_url(
                    self.config.taskit.base_url, cfg.microsandbox_host_ip
                )
                context["taskit_base_url"] = self.config.taskit.base_url
            # forkd (a container sharing the host kernel) launches Chromium in-guest.
            chrome_executable_path = "/usr/bin/chromium" if cfg.run_in_forkd and has_chrome_devtools else None
            chrome_args = ["--no-sandbox", "--disable-dev-shm-usage"] if cfg.run_in_forkd and has_chrome_devtools else None
            chrome_isolated = bool(cfg.run_in_forkd and has_chrome_devtools)
            # microsandbox (an aarch64 libkrun microVM) cannot launch Chromium — it
            # SIGTRAPs in early bring-up regardless of flags. Instead the in-guest MCP
            # connects to a browser running on the host over CDP. 127.0.0.1 is rewritten
            # to the host LAN IP when the config is staged into the guest.
            chrome_browser_url = (
                _microsandbox_browser_url(self.config)
                if cfg.sandbox_mode == "microsandbox" and not cfg.run_in_forkd and has_chrome_devtools
                else None
            )
            mcp_config = self._generate_mcp_config(
                task_id, agent_name, log_dir, working_dir=working_dir, model=model, mcps=mcps,
                mcp_env_override=mcp_env_override,
                chrome_executable_path=chrome_executable_path,
                chrome_args=chrome_args,
                chrome_isolated=chrome_isolated,
                chrome_browser_url=chrome_browser_url,
            )
            if mcp_config:
                context["mcp_config"] = mcp_config

            if cfg.run_in_forkd and mcp_available:
                wd = Path(working_dir)
                include_paths = []
                for rel in (
                    ".codex/config.toml",
                    ".gemini/settings.json",
                    ".mcp.json",
                    ".qwen/settings.json",
                    ".kilocode/mcp.json",
                    "opencode.json",
                ):
                    candidate = wd / rel
                    if candidate.exists():
                        include_paths.append(str(candidate))
                if include_paths:
                    context["forkd_include_paths"] = include_paths

            # Pass MCP env dict so harnesses can inject CLI flags (e.g. Codex -c)
            if has_taskit:
                context["mcp_env"] = mcp_env_override or self._get_mcp_env(task_id, agent_name, model=model)
            if has_mobile:
                context["mobile_mcp_enabled"] = True
            if has_chrome_devtools:
                context["chrome_devtools_mcp_enabled"] = True
                context["chrome_devtools_headless"] = bool(
                    self.config.chrome_devtools and self.config.chrome_devtools.headless
                )
                if cfg.run_in_forkd:
                    context["forkd_require_chrome"] = True
                    context["chrome_devtools_executable_path"] = "/usr/bin/chromium"
                    context["chrome_devtools_chrome_args"] = ["--no-sandbox", "--disable-dev-shm-usage"]
                    context["chrome_devtools_isolated"] = True
                elif chrome_browser_url:
                    context["chrome_devtools_browser_url"] = chrome_browser_url
            if mcp_available:
                # Claude Code needs --allowedTools CLI flag for MCP tool permissions
                allowed: List[str] = []
                if has_taskit:
                    from odin.mcps.taskit_mcp.config import claude_tool_names
                    allowed.extend(claude_tool_names())
                if has_mobile:
                    from odin.mcps.mobile_mcp.config import claude_mobile_tool_names
                    allowed.extend(claude_mobile_tool_names())
                if has_chrome_devtools:
                    from odin.mcps.chrome_devtools_mcp.config import claude_chrome_devtools_tool_names
                    allowed.extend(claude_chrome_devtools_tool_names())
                context["mcp_allowed_tools"] = allowed

            skip_proof = bool(task_obj and task_obj.metadata.get("board_skip_proof"))

            # Only inject the TaskIt-MCP proof-of-work instructions for agents
            # that are actually wired to consume an MCP config. An agent told
            # to call taskit_add_comment with no MCP config available will spin
            # up a subagent to do it and hang to timeout (seen with agy #147
            # before it was wired into MCP_CONFIG_MAP). Membership in
            # MCP_CONFIG_MAP is the single signal that the agent can both
            # receive a config and the prompt section that tells it how to use
            # the taskit tools. Every confined provider is now a member.
            from odin.mcps.taskit_mcp.config import MCP_CONFIG_MAP as _MCP_CAPABLE
            agent_mcp_capable = agent_name in _MCP_CAPABLE
            effective_mcp = mcp_available and agent_mcp_capable

            # Advisor trial (routing-and-cost bucket): opt-in via config,
            # scoped to the trial's agent roster (default glm/minimax) so
            # the cap-and-consult mechanism only fires where it's being
            # measured.
            advisor_cfg = self.config.advisor
            advisor_enabled_for_task = bool(
                advisor_cfg and advisor_cfg.enabled and working_dir
                and agent_name in advisor_cfg.trial_agents
            )

            wrapped = self._wrap_prompt(
                prompt, working_dir,
                mcp_task_id=task_id if effective_mcp else None,
                mcps=mcps if effective_mcp else None,
                skip_proof=skip_proof,
                advisor_enabled=advisor_enabled_for_task,
                advisor_max_consults=advisor_cfg.max_consults if advisor_cfg else advisor.DEFAULT_MAX_CONSULTS,
                project_notes=read_project_notes(
                    working_dir, self.config.project_notes_path
                ),
            )

            # Log effective input as debug comment for DAG debugging
            if not mock:
                self.task_mgr.add_comment(
                    task_id=task_id,
                    author="odin",
                    content=f"Effective input (with upstream context):\n\n{wrapped[:8000]}",
                    attachments=["debug:effective_input"],
                )

            # Build per-task env vars for opencode-type agents so that
            # parallel tasks don't clobber each other's identity in the
            # shared opencode.json config file.
            tmux_env_vars: Optional[Dict[str, str]] = None
            if agent_name in ("minimax", "glm") and has_taskit:
                mcp_env = self._get_mcp_env(task_id, agent_name, model=model)
                tmux_env_vars = {
                    k: v for k, v in mcp_env.items()
                    if k in ("TASKIT_TASK_ID", "TASKIT_AUTHOR_EMAIL", "TASKIT_AUTHOR_LABEL")
                }
                if skip_proof:
                    self.task_mgr.add_comment(
                        task_id=task_id,
                        author="odin",
                        content="Proof collection is disabled for this board. This task will not include screenshot proof or proof comments.",
                    )

            # Advisor watcher: polls the worktree for a stuck-agent question
            # while the harness runs, answers (capped) via a strong-model
            # consult, and writes .odin/advice.md for the agent to read.
            advisor_watcher: Optional[advisor.AdvisorWatcher] = None
            advisor_watcher_task = None
            if advisor_enabled_for_task:
                advisor_watcher = advisor.AdvisorWatcher(
                    working_dir=working_dir,
                    consult_fn=self._make_advisor_consult_fn(advisor_cfg, working_dir),
                    max_consults=advisor_cfg.max_consults,
                    task_title=getattr(task_obj, "title", "") if task_obj else "",
                )
                advisor_watcher_task = asyncio.ensure_future(advisor_watcher.run())

            # Try tmux-based execution for CLI harnesses
            try:
                cmd = harness.build_execute_command(wrapped, context)
                if cmd and tmux.is_available():
                    raw_result = await self._execute_via_tmux(
                        task_id,
                        cmd,
                        working_dir,
                        output_file,
                        harness.name,
                        timeout_seconds=self.config.execution_timeout_seconds,
                        env_vars=tmux_env_vars,
                    )
                else:
                    # Fallback: direct harness execution (API or no tmux)
                    execute_task = asyncio.ensure_future(harness.execute(wrapped, context))

                    # Brief delay to let the subprocess spawn and set _current_pid
                    await asyncio.sleep(0.1)
                    if not mock and harness._current_pid:
                        task_obj = self.task_mgr.get_task(task_id)
                        if task_obj:
                            task_obj.metadata["subprocess_pid"] = harness._current_pid
                            self.task_mgr.update_task(task_obj)

                    raw_result = await execute_task
            except Exception as exc:
                # Harness crashed — publish structured failure details via execution_result
                self._log.error(
                    "[task:%s] Execution crashed: %s", task_id, exc, exc_info=True,
                )
                if not mock:
                    raw_jsonl_for_backend = ""
                    trace_path = Path(trace_file)
                    output_path = Path(output_file)
                    if trace_path.exists() and trace_path.stat().st_size > 0:
                        raw_jsonl_for_backend = trace_path.read_text()
                    elif output_path.exists() and output_path.stat().st_size > 0:
                        raw_jsonl_for_backend = output_path.read_text()

                    failure_meta = self._classify_failure(exc, phase="task_execution")
                    stack_excerpt = self._sanitize_trace_excerpt(
                        traceback.format_exc(),
                        limit=1200,
                    )
                    error_message = f"Execution crashed: {type(exc).__name__}: {exc}"
                    crash_metadata = {
                        "selected_model": model,
                        **({"taskit_run_token": task_run_token} if task_run_token else {}),
                        "failure_debug": stack_excerpt,
                    }
                    # Best-effort: capture whatever the stream said before the
                    # crash (finish reason, tokens) so the failure record is
                    # actionable even when the orchestrator itself raised.
                    try:
                        crash_summary = extract_stream_summary(raw_jsonl_for_backend)
                        if crash_summary:
                            crash_metadata["stream_summary"] = crash_summary
                    except Exception:
                        self._log.debug(
                            "[task:%s] stream_summary extraction failed on crash path",
                            task_id, exc_info=True,
                        )
                    execution_payload = {
                        "success": False,
                        "raw_output": raw_jsonl_for_backend,
                        "effective_input": wrapped[:PAYLOAD_EFFECTIVE_INPUT_LIMIT],
                        "error": error_message[:PAYLOAD_ERROR_MESSAGE_LIMIT],
                        "duration_ms": None,
                        "agent": harness.name or agent_name,
                        "metadata": crash_metadata,
                        **failure_meta,
                    }
                    self.task_mgr.record_execution_result(
                        task_id=task_id,
                        execution_result=execution_payload,
                        status=TaskStatus.FAILED,
                        actor_email=self.task_mgr._format_actor_email(agent_name, model),
                    )
                raise
            finally:
                if advisor_watcher is not None:
                    advisor_watcher.stop()
                    try:
                        await asyncio.wait_for(advisor_watcher_task, timeout=5)
                    except Exception:
                        self._log.warning(
                            "[task:%s] advisor watcher did not stop cleanly", task_id, exc_info=True,
                        )

            # Record cost data (skip in mock mode)
            estimated_cost_usd = None
            if not mock:
                spec_id = task_obj.spec_id if task_obj else None
                cost_record = self.cost_tracker.record_task(
                    task_id=task_id,
                    spec_id=spec_id,
                    result=raw_result,
                    model=model,
                )
                estimated_cost_usd = cost_record.estimated_cost_usd

                # Advisor consult tokens recorded under the SAME task_id so
                # CostStore.summarize_task() folds them into the task's
                # total — the league table sees the trial's real cost.
                if advisor_watcher is not None and advisor_watcher.consults:
                    advisor.record_advisor_cost(
                        self.cost_tracker,
                        task_id=task_id,
                        spec_id=spec_id,
                        consults=advisor_watcher.consults,
                        model=advisor_cfg.model,
                    )

                # Store trace file path in task metadata
                if Path(trace_file).exists():
                    task_obj = self.task_mgr.get_task(task_id)
                    if task_obj:
                        task_obj.metadata["trace_file"] = trace_file
                        self.task_mgr.update_task(task_obj)

            result = raw_result
            agent_text = self._extract_agent_text(result.output or "")
            clean_output, parsed_success, summary = self._parse_envelope(agent_text)

            # Extract the run-close stream metadata ONCE (finish reason, token
            # counts, error events). It feeds two consumers: the truncation
            # signal the evidence ladder needs (task #332) and the failure
            # note enrichment below (task #330). Tolerant of every harness
            # format; never raises.
            run_stream_summary = self._read_run_stream_summary(
                trace_file, output_file, raw_result
            )

            # Task #331/#332: the pass/fail verdict is decided by the evidence
            # ladder, NOT by whether the model happened to print an
            # ODIN-STATUS line. An explicit verdict from any channel
            # (stdout block, status file, or an MCP proof comment) is
            # believed when present; otherwise the worktree delta vs the
            # HEAD recorded at run start decides — real edits go to REVIEW
            # flagged unconfirmed, an output-cap truncation with work goes to
            # resume-in-place, nothing at all fails with the true reason. The
            # status block is only a courtesy accelerator; a missing block
            # never fails a run.
            run_outcome = self._decide_task_outcome(
                raw_result=raw_result,
                parsed_success=parsed_success,
                working_dir=working_dir,
                start_head=worktree_start_head,
                start_dirty=worktree_start_dirty,
                task_id=task_id,
                comment_baseline=comment_baseline,
                stream_summary=run_stream_summary,
            )
            outcome_meta = dict(result.metadata) if result.metadata else {}
            if run_outcome.unconfirmed:
                outcome_meta["unconfirmed_completion"] = {
                    "reason": run_outcome.reviewer_note,
                    "start_head": worktree_start_head,
                }
            if run_outcome.no_agent_ran:
                outcome_meta["no_agent_ran"] = True
            # Task #332: a truncation-with-work outcome is a resumable failure.
            # Stamp the resume bookkeeping so (a) the next dispatch of this
            # task rebuilds a host-side resume prompt and (b) task #328's
            # routing table sees how many times this task has run out of
            # output budget. The cap itself is owned by the truncation
            # AUTO_REQUEUE policy (max_retries=2); the third truncation hits
            # it, stays FAILED, and routes to the policy.
            if run_outcome.resumable:
                prev_resume_count = int(
                    (task_obj.metadata or {}).get("truncation_resume_count", 0)
                ) if task_obj else 0
                outcome_meta["truncation_resume_count"] = prev_resume_count + 1
                outcome_meta["truncation_resume_pending"] = True
            result = TaskResult(
                success=run_outcome.success,
                output=clean_output if parsed_success is not None else result.output,
                error=None if run_outcome.success else run_outcome.error,
                duration_ms=result.duration_ms,
                agent=result.agent,
                metadata=outcome_meta,
            )

            # An unconfirmed completion goes to REVIEW, but the reviewer
            # must know the verdict was inferred from the diff — post a
            # visible flag so the human/agent reviewer judges the diff,
            # not the absent marker. Best-effort: never crash the run.
            if not mock and run_outcome.unconfirmed:
                try:
                    self.task_mgr.add_comment(
                        task_id=task_id,
                        author="odin",
                        content=(
                            "⚠️ UNCONFIRMED COMPLETION — "
                            f"{run_outcome.reviewer_note}. The worktree has real "
                            "changes since this run started; the run is routed to "
                            "REVIEW so the diff can be judged on its merits."
                        ),
                    )
                except Exception:
                    self._log.debug(
                        "[task:%s] could not post unconfirmed-completion flag",
                        task_id, exc_info=True,
                    )

            # Task #332: a resumable truncation is an operator-visible event —
            # the run hit the output cap with work in progress and will be
            # requeued into the same worktree. Best-effort: never crash the run.
            if not mock and run_outcome.resumable:
                resume_count = (result.metadata or {}).get("truncation_resume_count")
                try:
                    self.task_mgr.add_comment(
                        task_id=task_id,
                        author="odin",
                        content=(
                            "↻ TRUNCATED WITH WORK IN PROGRESS — the model hit "
                            "its output cap mid-task. The worktree holds real "
                            "changes, so this task will be requeued into the "
                            "same worktree with a host-built resume prompt "
                            f"(resume {resume_count}). The worktree survives; "
                            "no work is discarded."
                        ),
                    )
                except Exception:
                    self._log.debug(
                        "[task:%s] could not post truncation-resume flag",
                        task_id, exc_info=True,
                    )

            # Record a malformed ODIN-STATUS block into the ErrorEvent
            # ledger (W6.5 / task #237). The harness already inferred
            # success from observable work when possible; this call
            # makes the malformed-block occurrence visible to the
            # league-table query so the operator can see which model
            # is emitting garbage. Best-effort: a recording failure
            # must never crash the live task path.
            if not mock:
                malformed = (result.metadata or {}).get("malformed_status")
                if malformed:
                    try:
                        from tasks.errors import record_agent_malformed_status  # noqa: WPS433
                        record_agent_malformed_status(
                            task=task_obj,
                            symptom=(
                                f"ODIN-STATUS block value was {malformed.get('raw_block')!r}"
                                + (" (inferred success)" if malformed.get("inferred") else "")
                            ),
                            raw_block=str(malformed.get("raw_block") or ""),
                            agent=agent_name or "",
                            model=model or "",
                            inferred=bool(malformed.get("inferred")),
                        )
                    except Exception as exc:
                        self._log.debug(
                            "[task:%s] malformed-status recording failed; run continues",
                            task_id, exc_info=exc,
                        )

            # Send raw result transparently — taskit owns all processing
            if not mock:
                # Read raw JSONL from trace file for backend processing
                raw_jsonl_for_backend = ""
                trace_path = Path(trace_file)
                output_path = Path(output_file)
                if trace_path.exists() and trace_path.stat().st_size > 0:
                    raw_jsonl_for_backend = trace_path.read_text()
                elif output_path.exists() and output_path.stat().st_size > 0:
                    raw_jsonl_for_backend = output_path.read_text()
                else:
                    raw_jsonl_for_backend = result.output or result.error or ""

                new_status = TaskStatus.REVIEW if result.success else TaskStatus.FAILED
                payload_metadata = dict(result.metadata) if result.metadata else {}
                if estimated_cost_usd is not None:
                    payload_metadata["estimated_cost_usd"] = estimated_cost_usd
                if task_run_token:
                    payload_metadata["taskit_run_token"] = task_run_token

                # Reuse the stream metadata already extracted before the
                # verdict (finish reason, token counts, last event timestamp,
                # error events) so the failure comment names the *real* reason
                # instead of a generic truncation guess.
                stream_summary: Dict[str, Any] = run_stream_summary or {}
                payload_error = result.error
                try:
                    if stream_summary:
                        payload_metadata["stream_summary"] = stream_summary
                    # Enrich the generic truncation error with the concrete
                    # reason + token counts so the board comment is actionable.
                    if (
                        not result.success
                        and payload_error
                        and "truncated mid-generation" in payload_error.lower()
                    ):
                        note = format_stream_summary_note(stream_summary)
                        if note:
                            payload_error = f"{payload_error} {note}"
                except Exception:
                    self._log.debug(
                        "[task:%s] stream_summary extraction failed; run continues",
                        task_id, exc_info=True,
                    )

                # Add structured failure fields so the UI can show
                # actionable failure details (type, origin) — not just a
                # bare error string.
                failure_fields: Dict[str, str] = {}
                if not result.success and payload_error:
                    error_lower = payload_error.lower()
                    if "timed out" in error_lower or "timeout" in error_lower:
                        f_type = "timeout"
                    elif "not found on path" in error_lower:
                        f_type = "cli_not_found"
                    elif (
                        "model" in error_lower
                        and any(tok in error_lower for tok in ["not supported", "unsupported", "invalid_request_error"])
                    ):
                        f_type = "model_escalation_failure"
                    elif "exited with code" in error_lower:
                        f_type = "agent_execution_failure"
                    else:
                        f_type = "agent_execution_failure"
                # Add structured failure fields so the UI + the backend
                # classifier get an actionable failure_type. Task #331: the
                # type comes from the evidence ladder's verdict, not from
                # re-parsing the error string. A pre-execution failure
                # (no agent ran) is tagged as its own class so the routing
                # policy retries the infra failure instead of blaming/
                # escalating an agent that never ran.
                failure_fields: Dict[str, str] = {}
                if not result.success:
                    f_type = run_outcome.failure_type or "agent_execution_failure"
                    origin = (
                        "orchestrator:pre_execution"
                        if run_outcome.no_agent_ran
                        else "orchestrator:task_execution"
                    )
                    # The ladder owns the coarse verdict + the infra /
                    # silent / timeout types; sharpen a genuine agent
                    # failure's label when the harness reported a concrete
                    # error (CLI missing, model unsupported) so the UI and
                    # the backend classifier keep their distinct classes.
                    if not run_outcome.no_agent_ran and result.error:
                        el = result.error.lower()
                        if "not found on path" in el:
                            f_type = "cli_not_found"
                        elif "model" in el and any(
                            tok in el for tok in ("not supported", "unsupported", "invalid_request_error")
                        ):
                            f_type = "model_escalation_failure"
                    failure_fields = {
                        "failure_type": f_type,
                        "failure_reason": (result.error or "")[:PAYLOAD_ERROR_MESSAGE_LIMIT],
                        "failure_origin": origin,
                    }

                self.task_mgr.record_execution_result(
                    task_id=task_id,
                    execution_result={
                        "success": result.success,
                        "raw_output": raw_jsonl_for_backend,
                        "effective_input": wrapped[:PAYLOAD_EFFECTIVE_INPUT_LIMIT],
                        "error": payload_error,
                        "duration_ms": result.duration_ms,
                        "agent": result.agent or agent_name,
                        "metadata": payload_metadata,
                        **failure_fields,
                    },
                    status=new_status,
                    actor_email=self.task_mgr._format_actor_email(agent_name, model),
                )

                self.logger.log(
                    action="execution_result_posted",
                    metadata={
                        "task_id": task_id,
                        "success": result.success,
                        "duration_ms": result.duration_ms,
                        "model": model,
                        "agent": agent_name,
                        "new_status": new_status.value if hasattr(new_status, "value") else str(new_status),
                    },
                )

                # Post raw JSONL trace as visible comment for debugging
                raw_jsonl = ""
                trace_path = Path(trace_file)
                output_path = Path(output_file)
                if trace_path.exists() and trace_path.stat().st_size > 0:
                    raw_jsonl = trace_path.read_text()
                elif output_path.exists() and output_path.stat().st_size > 0:
                    raw_jsonl = output_path.read_text()
                else:
                    raw_jsonl = result.output or result.error or ""
                if raw_jsonl.strip():
                    self.task_mgr.add_comment(
                        task_id=task_id,
                        author="odin",
                        content=raw_jsonl,
                        attachments=["trace:execution_jsonl"],
                    )

            if result.success:
                self._log.info(
                    "[task:%s] Completed: agent=%s, duration=%.1fs",
                    task_id, agent_name,
                    (result.duration_ms or 0) / 1000,
                )
            else:
                self._log.warning(
                    "[task:%s] Failed: agent=%s, duration=%.1fs, error=%s",
                    task_id, agent_name,
                    (result.duration_ms or 0) / 1000,
                    (result.error or "unknown")[:200],
                )
            self.logger.log(
                action="task_completed" if result.success else "task_failed",
                task_id=task_id,
                agent=agent_name,
                output=result.output[:500] if result.output else None,
                duration_ms=result.duration_ms,
            )

            return {
                "task_id": task_id,
                "agent": agent_name,
                "success": result.success,
                "output": result.output,
                "error": result.error,
            }


def _extract_title(spec_text: str) -> str:
    """Extract a title from spec text (first heading or first line)."""
    for line in spec_text.strip().splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
        if line:
            return line[:80]
    return "Untitled spec"
