"""Odin CLI entry point.

Odin is a task-board orchestration system. It decomposes tasks, assigns
them to AI agents, and tracks their progress — like a Trello board where
tasks evolve, get reassigned, and accumulate history via comments.

TaskIt is the isolated task-management backend; Odin communicates with it
to create, assign, and track tasks.  Dependency resolution and scheduling
belong to TaskIt+Celery; odin is a single-task executor.

Staged workflow:
    odin plan <spec_file>         Decompose + suggest assignments (no execution)
    odin status                   Review tasks and assignments
    odin assign <task_id> <agent> Reassign a task to a different agent
    odin exec <task_id>           Execute a single task (foreground)
    odin exec <task_id> --mock    Execute with mock harness (no backend writes)
    odin attach <task_id>         Attach to a running task's tmux session
    odin logs                     Show latest run log (last 50 lines)
    odin logs <task_id>            Run log filtered to task
    odin logs debug                Show odin_detail.log (tracebacks)
    odin logs -f                   Follow all running tasks
    odin logs <task_id> -f         Follow a single task's output
    odin logs debug -f             Tail odin_detail.log
    odin logs -n 100               Control line count
    odin logs -b <board_id>        Resolve project via board registry
    odin tail [task_id]            (deprecated → odin logs --follow)
    odin stop <task_id>           Stop a running task
    odin watch                    Auto-refreshing status dashboard
    odin show <task_id>           Show full task details

Spec management:
    odin specs                    List all specs with derived status
    odin spec show <id>           Show spec details and its tasks
    odin spec abandon <id>        Mark a spec as abandoned

Label management:
    odin label list               List all labels
    odin label create <name> <color>  Create a label

All-in-one shortcut:
    odin run <spec_file>          plan + dispatch (Celery runs tasks)
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import shutil

import fire
from rich.console import Console
from rich.table import Table

from odin.config import load_config
from odin.cost_tracking import CostStore
from odin.logging import setup_logger
from odin.models import OdinConfig
from odin.orchestrator import Orchestrator
from odin.specs import SpecStore, derive_spec_status, spec_short_tag
from odin.taskit import TaskManager
from odin.taskit.models import TaskStatus
from odin import tmux

console = Console()


class OdinCLI:
    """Odin - Task-board orchestration CLI.

    Tasks evolve: they get created, assigned, reassigned, executed, and
    accumulate comments — like cards on a Trello board. TaskIt manages the
    task state; Odin is a single-task executor.

    Staged workflow:
        odin plan <spec_file>             Decompose + suggest assignments
        odin status                       Review tasks and assignments
        odin assign <task_id> <agent>     Reassign before execution
        odin set-model <task_id> <model>  Override model for a task
        odin exec <task_id>               Execute a single task (foreground)
        odin exec <task_id> --mock        Mock mode (no backend writes)
        odin attach <task_id>             Attach to running task's tmux session
        odin tail [task_id]               Follow live output
        odin stop <task_id>               Stop a running task
        odin watch                        Auto-refreshing dashboard
        odin show <task_id>               Full task details

    Spec management:
        odin specs                        List all specs with derived status
        odin spec show <id>               Show spec content + its tasks
        odin spec abandon <id>            Mark a spec as abandoned

    Label management:
        odin label list                   List all labels
        odin label create <name> <color>  Create a label

    All-in-one:
        odin run <spec_file>              plan + dispatch (Celery runs tasks)

    Other:
        odin guide                        Show sample workflow walkthrough
        odin test [suite]                 Run tests (quick, plan, e2e, or all)
        odin mcp_config [task_id]         Generate per-CLI MCP configs for manual testing
        odin logs [task_id]               View structured logs
        odin config                       Show configuration

    Global flags:
        --config PATH   Path to a config YAML file
    """

    def __init__(self, config: Optional[str] = None):
        """
        Args:
            config: Path to a YAML config file. If not given, searches
                    .odin/config.yaml (project-local) then ~/.odin/config.yaml.
        """
        self._config_path = config
        self._config: Optional[OdinConfig] = None

    def _get_config(self) -> OdinConfig:
        if self._config is None:
            self._config = load_config(self._config_path)
            self._cli_log = setup_logger("odin.cli", log_dir=self._config.log_dir)
            self._cli_log.info(
                "CLI initialized: config=%s, backend=%s",
                self._config.config_source, self._config.board_backend,
            )
        return self._config

    def _get_task_manager(self) -> TaskManager:
        """Create a TaskManager with the appropriate backend.

        Mirrors the backend initialization from Orchestrator.__init__()
        so that CLI commands use the same backend as plan/exec.
        """
        cfg = self._get_config()
        backend = None
        if cfg.board_backend != "local":
            from odin.backends.registry import get_backend
            spec_dir = str(Path(cfg.task_storage).parent / "specs")
            backend_kwargs = {"task_storage": cfg.task_storage, "spec_storage": spec_dir}
            if cfg.board_backend == "taskit" and cfg.taskit:
                backend_kwargs.update(cfg.taskit.model_dump())
            backend = get_backend(cfg.board_backend, **backend_kwargs)
        return TaskManager(cfg.task_storage, backend=backend)

    def _resolve_id(self, task_id: str) -> str:
        """Resolve a task ID prefix to a full ID."""
        mgr = self._get_task_manager()
        full = mgr.resolve_task_id(task_id)
        if not full:
            console.print(f"[red]Could not resolve task ID: {task_id}[/red]")
            console.print("[dim]No match or ambiguous prefix. Use 'odin status' to see IDs.[/dim]")
            raise SystemExit(1)
        return full

    def _get_spec_store(self) -> SpecStore:
        cfg = self._get_config()
        spec_dir = str(Path(cfg.task_storage).parent / "specs")
        return SpecStore(spec_dir)

    def _get_cost_store(self) -> CostStore:
        cfg = self._get_config()
        return CostStore(cfg.cost_storage)

    def _get_odin_dir(self) -> str:
        """Return the .odin directory path (parent of task_storage)."""
        cfg = self._get_config()
        return str(Path(cfg.task_storage).parent)

    # ------------------------------------------------------------------
    # init
    # ------------------------------------------------------------------

    def init(self, force: bool = False, board_id: Optional[int] = None, base_url: Optional[str] = None):
        """Initialize .odin/ directory with sample config.

        Creates .odin/ with config.yaml, tasks/, logs/, and specs/ subdirs.
        Warns if .odin/config.yaml already exists (use --force to overwrite).

        Args:
            force: Overwrite existing config.yaml and all generated files.
            board_id: TaskIt board ID to write into config.yaml.
            base_url: TaskIt backend URL to write into config.yaml.

        Example:
            odin init
            odin init --force
            odin init --board-id 42 --base-url http://localhost:8000
        """
        odin_dir = Path.cwd() / ".odin"
        config_dest = odin_dir / "config.yaml"

        if config_dest.exists() and not force:
            console.print(
                f"[yellow]Warning: {config_dest} already exists. Skipping config copy.[/yellow]\n"
                "[dim]Use [bold]odin init --force[/bold] to overwrite all config files.[/dim]"
            )
        else:
            odin_dir.mkdir(parents=True, exist_ok=True)
            # Find config.sample.yaml relative to the package
            sample = Path(__file__).resolve().parent.parent.parent / "config" / "config.sample.yaml"
            if not sample.exists():
                # Fallback search
                for candidate in [
                    Path.cwd() / "config" / "config.sample.yaml",
                    Path.cwd() / "odin" / "config" / "config.sample.yaml",
                ]:
                    if candidate.exists():
                        sample = candidate
                        break

            if sample.exists():
                shutil.copy2(sample, config_dest)
                console.print(f"[green]Created[/green] {config_dest}")
            else:
                console.print(
                    "[yellow]Sample config not found — creating empty config.[/yellow]"
                )
                config_dest.write_text("# Odin config — see odin docs for options\n")
                console.print(f"[green]Created[/green] {config_dest}")

        # Overlay board_id / base_url into config.yaml if provided
        if board_id is not None or base_url is not None:
            import yaml
            try:
                config_data = yaml.safe_load(config_dest.read_text()) or {}
                if board_id is not None:
                    config_data["board_id"] = board_id
                    # Also update the nested taskit section so the API uses the same ID
                    if "taskit" not in config_data:
                        config_data["taskit"] = {}
                    config_data["taskit"]["board_id"] = board_id
                if base_url is not None:
                    config_data["base_url"] = base_url
                    if "taskit" not in config_data:
                        config_data["taskit"] = {}
                    config_data["taskit"]["base_url"] = base_url
                config_dest.write_text(yaml.dump(config_data, default_flow_style=False))
                console.print(f"[green]Configured[/green] board_id={board_id}, base_url={base_url}")
            except Exception as exc:
                console.print(f"[red]Failed to write board config:[/red] {exc}")

        # Register in global board registry so `odin logs -b <id>` works
        if board_id is not None:
            from odin.board_registry import register_board
            register_board(board_id, str(Path.cwd()), name=Path.cwd().name)
            console.print(f"[green]Registered[/green] board {board_id} in ~/.odin/boards.json")

        # Create subdirectories
        for subdir in ["tasks", "logs", "specs"]:
            d = odin_dir / subdir
            d.mkdir(parents=True, exist_ok=True)
            console.print(f"[green]Created[/green] {d}/")

        # Create MCP config files for all 6 agent CLIs
        cfg = self._get_config()
        taskit_env = {"TASKIT_URL": cfg.taskit.base_url} if cfg.taskit else {}
        created = _generate_all_mcp_configs(Path.cwd(), taskit_env, mcps=cfg.mcps)
        for path in created:
            console.print(f"[green]Created[/green] {path}")

        # Create Claude Code permissions so odin exec doesn't prompt for approval
        claude_settings_path = _generate_claude_settings(Path.cwd(), mcps=cfg.mcps)
        if claude_settings_path:
            console.print(f"[green]Created[/green] {claude_settings_path}")

        # Create .env.example with auth template
        env_example_path = Path.cwd() / ".env.example"
        if not env_example_path.exists() or force:
            env_example_path.write_text(
                "# TaskIt Authentication\n"
                "# Required when TaskIt has FIREBASE_AUTH_ENABLED=True\n"
                "# The admin user must exist in TaskIt with is_admin=True\n"
                "ODIN_ADMIN_USER=\n"
                "ODIN_ADMIN_PASSWORD=\n"
                "\n"
                "# Firebase API key — used by TaskIt backend for email/password auth\n"
                "ODIN_FIREBASE_API_KEY=\n"
            )
            console.print(f"[green]Created[/green] {env_example_path}")

        console.print(
            "\n[bold green]Initialized .odin/ directory.[/bold green]\n"
            "[dim]Edit .odin/config.yaml to customize agent settings and model routing.[/dim]"
        )

        # TaskIt auth setup guidance
        console.print("\n[bold]TaskIt Authentication:[/bold]")
        console.print(
            "  If your TaskIt backend has authentication enabled, copy and configure [cyan].env.example[/cyan]:"
        )
        console.print("    [dim]cp .env.example .env  # then edit .env with your values[/dim]")
        console.print("")
        console.print("    [cyan]ODIN_ADMIN_USER[/cyan]=admin@test.com")
        console.print("    [cyan]ODIN_ADMIN_PASSWORD[/cyan]=test123")
        console.print("    [cyan]ODIN_FIREBASE_API_KEY[/cyan]=your_firebase_api_key")
        console.print(
            "\n  Create the admin user on the TaskIt backend first:"
        )
        console.print(
            "    [dim]python manage.py createadmin --email admin@test.com --password test123[/dim]"
        )

        # MCP integration guidance
        console.print("\n[bold]MCP Integration:[/bold]")
        console.print(
            "  Per-CLI config files were created so each agent CLI auto-discovers"
        )
        console.print(
            "  TaskIt MCP tools (.mcp.json, .gemini/, .qwen/, .codex/, .kilocode/, opencode.json)."
        )
        console.print(
            "  Auth uses the same [cyan].env[/cyan] vars as odin (ODIN_ADMIN_USER, etc.)."
        )
        console.print(
            "  Use [cyan]odin mcp_config <task_id>[/cyan] to scope tools to a specific task."
        )

        # Onboarding guidance
        console.print("\n[bold]Getting Started:[/bold]")
        console.print("  [cyan]odin plan <spec.md>[/cyan]      Decompose a spec into tasks (interactive)")
        console.print("  [cyan]odin plan <spec.md> --auto[/cyan] One-shot planning (non-interactive)")
        console.print("  [cyan]odin plan <spec.md> --quick[/cyan] Direct plan, no codebase exploration")
        console.print("  [cyan]odin status[/cyan]              View task status and assignments")
        console.print("  [cyan]odin assign <id> <agent>[/cyan]  Reassign a task before execution")
        console.print("  [cyan]odin exec[/cyan]                Execute planned tasks (background)")
        console.print("  [cyan]odin run <spec.md>[/cyan]       Plan and execute in one step")
        console.print("\n[dim]Tip: Start with [bold]odin plan[/bold] to create tasks from a spec.[/dim]")

    # ------------------------------------------------------------------
    # plan
    # ------------------------------------------------------------------

    def plan(
        self,
        spec_file: Optional[str] = None,
        prompt: Optional[str] = None,
        auto: bool = False,
        quiet: bool = False,
        quick: bool = False,
        base_agent: Optional[str] = None,
    ):
        """Decompose a spec into sub-tasks and suggest agent assignments.

        By default, opens an interactive tmux session where you chat with
        the planning agent. When you exit, odin extracts tasks from the
        transcript.

        Use --auto to skip the interactive session and decompose directly.

        Does NOT execute anything. Review with 'odin status', reassign with
        'odin assign', then execute with 'odin exec'.

        Examples:
            odin plan specs/poem_spec.md              Interactive (default)
            odin plan specs/poem_spec.md --auto        One-shot decomposition
            odin plan specs/poem_spec.md --quiet       One-shot with spinner
            odin plan specs/poem_spec.md --quick       Direct plan, no exploration
            odin plan --prompt "Write a haiku" --auto
            odin plan spec.md --auto --base-agent codex

        Args:
            spec_file: Path to a markdown spec file.
            prompt: Inline prompt string (alternative to spec_file).
            auto: Skip interactive session; decompose directly (one-shot).
            quiet: Suppress streaming output and show a spinner instead
                (implies --auto).
            quick: Skip codebase exploration; the agent generates the plan
                directly from the spec without reading files.
            base_agent: Override which agent does decomposition (e.g. codex).
        """
        if not spec_file and not prompt:
            console.print("[red]Provide either a spec file or --prompt.[/red]")
            console.print('[dim]Usage: odin plan <spec_file>  or  odin plan --prompt "..."[/dim]')
            return

        if spec_file:
            path = Path(spec_file)
            if not path.exists():
                console.print(f"[red]Spec file not found: {spec_file}[/red]")
                return
            spec = path.read_text()
            console.print(f"[dim]Loaded spec from {spec_file}[/dim]")
        else:
            spec = prompt

        cfg = self._get_config()
        self._cli_log.info(
            "plan: spec_file=%s, auto=%s, quick=%s, base_agent=%s",
            spec_file, auto, quick, base_agent,
        )
        if base_agent:
            cfg.base_agent = base_agent
            console.print(f"[dim]Using base agent override: {base_agent}[/dim]")
        orch = Orchestrator(cfg)

        if quiet:
            # Quiet mode implies auto — spinner, no streaming
            console.print("[bold]Decomposing and planning...[/bold]")
            with console.status("[bold green]Planning..."):
                spec_id, tasks = asyncio.run(
                    orch.plan(spec, spec_file=spec_file, mode="quiet", quick=quick)
                )
        elif auto:
            from odin.harnesses.base import extract_text_from_line

            def _stream_chunk(chunk: str) -> None:
                text = extract_text_from_line(chunk)
                if text:
                    sys.stdout.write(text)
                    sys.stdout.flush()

            console.print(f"[bold]Planning with {cfg.base_agent}...[/bold]\n")
            spec_id, tasks = asyncio.run(
                orch.plan(spec, spec_file=spec_file, mode="auto", stream_callback=_stream_chunk, quick=quick)
            )
            # Ensure a newline after streamed output
            sys.stdout.write("\n")
        else:
            # Interactive mode (default): tmux session with agent
            spec_id, tasks = asyncio.run(
                orch.plan(spec, spec_file=spec_file, mode="interactive", quick=quick)
            )

        console.print(
            f"\n[bold green]Plan created![/bold green] "
            f"Spec [cyan]{spec_id}[/cyan] with {len(tasks)} tasks:\n"
        )

        table = Table(title="Planned Tasks", show_lines=True)
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Title", max_width=40)
        table.add_column("Agent", style="green")
        table.add_column("Model", style="bright_green")
        table.add_column("Deps", style="magenta", no_wrap=True)
        table.add_column("Quota", style="yellow", no_wrap=True)
        table.add_column("Reasoning", style="dim", max_width=60)
        table.add_column("Status", style="blue")

        for t in tasks:
            reasoning = t.metadata.get("reasoning", "-") if t.metadata else "-"
            model = t.metadata.get("selected_model", "-") if t.metadata else "-"
            quota_snapshot = t.metadata.get("quota_snapshot") if t.metadata else None
            if quota_snapshot and "remaining_pct" in quota_snapshot:
                remaining = quota_snapshot["remaining_pct"]
                quota_str = f"{remaining:.0f}% left"
            else:
                quota_str = "-"
            deps_str = ", ".join(d[:8] for d in t.depends_on) if t.depends_on else "-"
            table.add_row(
                t.id,
                t.title[:40],
                t.assigned_agent or "-",
                model,
                deps_str,
                quota_str,
                reasoning[:60] if reasoning != "-" else "-",
                t.status.value,
            )

        console.print(table)

        # Non-interactive modes (--auto, --quiet, --quick) with TaskIt backend:
        # auto-move all tasks to IN_PROGRESS so the DAG executor (Celery)
        # picks them up in dependency order
        if (quick or auto or quiet) and cfg.board_backend == "taskit":
            mgr = self._get_task_manager()
            moved = 0
            for t in tasks:
                if t.assigned_agent:
                    mgr.update_status(t.id, TaskStatus.IN_PROGRESS)
                    moved += 1
            if moved:
                console.print(
                    f"\n[bold green]Auto-queued {moved} tasks for execution.[/bold green]"
                )
                console.print(
                    "[dim]DAG executor will start them in dependency order. "
                    "Use 'odin watch' to monitor.[/dim]"
                )
            else:
                console.print(
                    "\n[yellow]No assigned tasks to queue. "
                    "Use 'odin assign' first.[/yellow]"
                )
        else:
            console.print(
                "\n[bold green]Planning complete.[/bold green] "
                "Review tasks with [bold]odin status[/bold]"
            )
            if cfg.board_backend == "taskit":
                board_url = f"{cfg.taskit.base_url.rstrip('/')}/boards/{cfg.taskit.board_id}/"
                console.print(f"[dim]Kanban board:[/dim] [link={board_url}]{board_url}[/link]")

    # ------------------------------------------------------------------
    # assign
    # ------------------------------------------------------------------

    def assign(self, task_id: str, agent: str):
        """Reassign a task to a different agent.

        Use 'odin status' to see current assignments, then reassign any
        task before execution.

        Examples:
            odin assign a1b2 gemini
            odin assign 8f104a78f19b codex

        Args:
            task_id: Task ID or unique prefix.
            agent: Agent name to assign (e.g. gemini, codex, qwen, claude).
        """
        full_id = self._resolve_id(task_id)
        cfg = self._get_config()

        # Validate agent exists
        if agent not in cfg.agents:
            console.print(f"[red]Unknown agent: {agent}[/red]")
            available = ", ".join(cfg.agents.keys())
            console.print(f"[dim]Available agents: {available}[/dim]")
            return

        mgr = self._get_task_manager()
        task = mgr.assign_task(full_id, agent)
        if task:
            console.print(
                f"[green]Reassigned[/green] {full_id[:8]} → [bold]{agent}[/bold]"
            )
        else:
            console.print(f"[red]Failed to assign task {task_id}[/red]")

    # ------------------------------------------------------------------
    # set_model
    # ------------------------------------------------------------------

    def set_model(self, task_id: str, model: str):
        """Override the model for a task before execution.

        Odin selects models automatically based on task complexity and
        agent config (default_model / premium_model). Use this command
        to override the automatic selection for a specific task.

        Examples:
            odin set-model a1b2 gemini-2.5-pro
            odin set-model d4e5 claude-sonnet-4-5

        Args:
            task_id: Task ID or unique prefix.
            model: Model name to use (e.g. gemini-2.5-pro, o3, claude-opus-4).
        """
        full_id = self._resolve_id(task_id)
        cfg = self._get_config()
        mgr = self._get_task_manager()
        task = mgr.get_task(full_id)

        if not task:
            console.print(f"[red]Task not found: {task_id}[/red]")
            return

        # Validate model belongs to the assigned agent
        if task.assigned_agent:
            agent_cfg = cfg.agents.get(task.assigned_agent)
            if agent_cfg and agent_cfg.models and model not in agent_cfg.models:
                available = ", ".join(agent_cfg.models.keys())
                console.print(
                    f"[yellow]Warning: '{model}' not in {task.assigned_agent}'s "
                    f"known models: {available}[/yellow]"
                )
                console.print("[dim]Proceeding anyway — model may still work.[/dim]")

        task.metadata["selected_model"] = model
        mgr.update_task(task)
        console.print(
            f"[green]Model set[/green] {full_id[:8]} → [bold bright_green]{model}[/bold bright_green]"
        )

    # ------------------------------------------------------------------
    # exec
    # ------------------------------------------------------------------

    def exec(self, task_id: str, mock: bool = False):
        """Execute a single task by ID.

        CLI tasks run inside named tmux sessions (odin-<id>) so you can
        attach/detach with 'odin attach <id>'. Falls back to inline
        subprocess execution when tmux is unavailable.

        Always runs in the foreground. For bulk execution with dependency
        resolution, use the TaskIt backend + Celery DAG executor.

        Examples:
            odin exec a1b2             Execute a single task
            odin exec a1b2 --mock      Execute with mock harness (no LLM, no backend writes)

        Args:
            task_id: Task ID or prefix to execute.
            mock: Use mock harness instead of real agents. Skips all
                backend writes (status changes, comments, cost tracking).
        """
        cfg = self._get_config()
        self._cli_log.info("exec: task_id=%s, mock=%s", task_id, mock)

        if mock:
            # Register mock harness for every configured agent name
            from odin.harnesses.mock import MockHarness
            from odin.harnesses.registry import HARNESS_REGISTRY
            for name in list(cfg.agents.keys()):
                HARNESS_REGISTRY[name] = MockHarness
            console.print("[bold yellow]Mock mode:[/bold yellow] using mock harness for all agents.")

        full_id = self._resolve_id(task_id)
        orch = Orchestrator(cfg)

        console.print(f"[bold]Executing task {full_id[:8]}...[/bold]")
        try:
            with console.status("[bold green]Executing..."):
                result = asyncio.run(orch.exec_task(full_id, mock=mock))

            if result["success"]:
                console.print(f"[green]Task {full_id[:8]} completed.[/green]")
                preview = result["output"][:200] if result["output"] else ""
                if preview:
                    console.print(f"[dim]Preview: {preview}[/dim]")
            else:
                error_msg = result.get("error", "unknown error")
                console.print(
                    f"[red]Task {full_id[:8]} failed:[/red]\n{error_msg}"
                )
        except (SystemExit, KeyboardInterrupt):
            console.print("\n[yellow]Execution interrupted.[/yellow]")
            if not mock:
                orch.mark_interrupted()

    # ------------------------------------------------------------------
    # summarize
    # ------------------------------------------------------------------

    def summarize(self, task_id: str):
        """Generate an AI summary of a task's comment history.

        Reads all comments on the task, runs them through the task's
        assigned agent, and posts the result as a summary comment.

        Examples:
            odin summarize a1b2
            odin summarize 42

        Args:
            task_id: Task ID or unique prefix.
        """
        cfg = self._get_config()
        self._cli_log.info("summarize: task_id=%s", task_id)

        full_id = self._resolve_id(task_id)
        orch = Orchestrator(cfg)

        console.print(f"[bold]Summarizing task {full_id[:8]}...[/bold]")
        try:
            with console.status("[bold green]Summarizing..."):
                result = asyncio.run(orch.summarize_task(full_id))

            if result["success"]:
                console.print(f"[green]Summary posted for task {full_id[:8]}.[/green]")
                preview = result.get("summary", "")[:200]
                if preview:
                    console.print(f"[dim]{preview}[/dim]")
            else:
                error_msg = result.get("error", "unknown error")
                console.print(
                    f"[red]Summarize failed for task {full_id[:8]}:[/red]\n{error_msg}"
                )
        except Exception as exc:
            console.print(f"[red]Summarize failed:[/red] {exc}")
            raise SystemExit(1)

    # ------------------------------------------------------------------
    # reflect
    # ------------------------------------------------------------------

    def reflect(
        self,
        task_id: str,
        report_id: Optional[str] = None,
        model: str = "claude-opus-4-6",
        agent: str = "claude",
    ):
        """Run a reflection audit on a completed task.

        Gathers task context, runs a reviewer agent in read-only mode,
        and submits a structured report back to TaskIt. Typically invoked
        by the TaskIt backend as a subprocess (not directly by users).

        Examples:
            odin reflect 42 --report-id 7 --model claude-opus-4-6
            odin reflect 42 --report-id 7 --agent gemini --model gemini-2.5-pro

        Args:
            task_id: Task ID to reflect on.
            report_id: ReflectionReport ID in TaskIt (required for result submission).
            model: Reviewer model to use.
            agent: Reviewer agent harness name.
        """
        if not report_id:
            console.print("[red]--report-id is required.[/red]")
            raise SystemExit(1)

        cfg = self._get_config()
        self._cli_log.info(
            "reflect: task_id=%s, report_id=%s, model=%s, agent=%s",
            task_id, report_id, model, agent,
        )

        taskit_url = ""
        if cfg.taskit:
            taskit_url = cfg.taskit.base_url

        if not taskit_url:
            console.print("[red]TaskIt backend not configured.[/red]")
            raise SystemExit(1)

        console.print(
            f"[bold]Reflecting on task {task_id} with {agent}/{model}...[/bold]"
        )

        from odin.reflection import reflect_task
        reflect_task(
            task_id=str(task_id),
            report_id=str(report_id),
            model=model,
            agent=agent,
            taskit_url=taskit_url,
            log_dir=cfg.log_dir,
        )

        console.print(f"[green]Reflection complete for task {task_id}.[/green]")

    # ------------------------------------------------------------------
    # attach
    # ------------------------------------------------------------------

    def attach(self, task_id: str):
        """Attach to a running task's tmux session.

        CLI tasks execute inside named tmux sessions. Use this command
        to attach and watch (or interact with) a running agent. Detach
        with Ctrl+B D — the task keeps running.

        Examples:
            odin attach a1b2

        Args:
            task_id: Task ID or unique prefix.
        """
        full_id = self._resolve_id(task_id)
        sess = tmux.session_name(full_id)
        result = subprocess.run(
            ["tmux", "has-session", "-t", sess],
            capture_output=True,
        )
        if result.returncode == 0:
            console.print(f"[dim]Attaching to session {sess}... (detach: Ctrl+B D)[/dim]")
            tmux.attach_sync(full_id)  # replaces current process
        else:
            console.print(f"[yellow]No active tmux session for task {full_id[:8]}.[/yellow]")
            console.print("[dim]The task may have finished. Use 'odin logs' for log output.[/dim]")

    # ------------------------------------------------------------------
    # tail
    # ------------------------------------------------------------------

    def tail(self, task_id: Optional[str] = None, lines: int = 20):
        """(Deprecated) Follow live output — use 'odin logs -f' instead.

        Args:
            task_id: Optional task ID or prefix.
            lines: Number of initial lines to show (default 20).
        """
        console.print("[dim]Note: 'odin tail' is now 'odin logs --follow'.[/dim]")
        self.logs(target=task_id, follow=True, n=lines)

    def _tail_file(self, path: Path, task_id: str, mgr: TaskManager, initial_lines: int):
        """Tail a single task's output file."""
        shown_bytes = 0

        while True:
            if path.exists():
                content = path.read_text()
                if len(content) > shown_bytes:
                    new_content = content[shown_bytes:]
                    if shown_bytes == 0:
                        # Show last N lines initially
                        all_lines = new_content.splitlines(keepends=True)
                        if len(all_lines) > initial_lines:
                            console.print(f"[dim]... ({len(all_lines) - initial_lines} lines skipped)[/dim]")
                            new_content = "".join(all_lines[-initial_lines:])
                    sys.stdout.write(new_content)
                    sys.stdout.flush()
                    shown_bytes = len(content)

            # Check if task is still running
            task = mgr.get_task(task_id)
            if task and task.status.value not in ("in_progress", "executing"):
                # Print any remaining content
                if path.exists():
                    final = path.read_text()
                    if len(final) > shown_bytes:
                        sys.stdout.write(final[shown_bytes:])
                        sys.stdout.flush()
                console.print(f"\n[dim]Task {task_id[:8]} finished ({task.status.value}).[/dim]")
                break

            time.sleep(0.5)

    def _tail_all(self, mgr: TaskManager, log_dir: Path, initial_lines: int, colors: list):
        """Tail all IN_PROGRESS tasks interleaved."""
        file_positions = {}  # task_id -> bytes read
        color_map = {}  # task_id -> color

        while True:
            tasks = mgr.list_tasks(status=TaskStatus.IN_PROGRESS)
            tasks += mgr.list_tasks(status=TaskStatus.EXECUTING)
            if not tasks:
                # Check if any tasks are assigned (about to start)
                assigned = mgr.list_tasks(status=TaskStatus.TODO)
                if not assigned:
                    console.print("[yellow]No running or assigned tasks.[/yellow]")
                    break
                time.sleep(1)
                continue

            for task in tasks:
                if task.id not in color_map:
                    color_map[task.id] = colors[len(color_map) % len(colors)]

                out_path = log_dir / f"task_{task.id}.out"
                if not out_path.exists():
                    continue

                content = out_path.read_text()
                prev_pos = file_positions.get(task.id, 0)
                if len(content) > prev_pos:
                    new_text = content[prev_pos:]
                    color = color_map[task.id]
                    agent = task.assigned_agent or "?"
                    prefix = f"[{color}][{task.id[:8]}:{agent}][/{color}] "
                    for line in new_text.splitlines(keepends=True):
                        sys.stdout.write(f"\033[{_ansi_color(color)}m[{task.id[:8]}:{agent}]\033[0m {line}")
                    sys.stdout.flush()
                    file_positions[task.id] = len(content)

            time.sleep(0.5)

        console.print("[dim]All tasks finished.[/dim]")

    def _show_run_log(self, log_dir: Path, task_filter: Optional[str], n: int):
        """Show the last N lines of the latest run_*.jsonl, optionally filtered to a task."""
        log_files = sorted(log_dir.glob("run_*.jsonl"), reverse=True)
        if not log_files:
            console.print("[yellow]No run log files found.[/yellow]")
            return

        latest = log_files[0]
        console.print(f"[dim]Log file: {latest}[/dim]\n")

        entries = []
        with open(latest) as fh:
            for line in fh:
                entry = json.loads(line)
                if task_filter and entry.get("task_id") and not entry["task_id"].startswith(task_filter):
                    continue
                entries.append(entry)

        if not entries:
            console.print("[yellow]No matching log entries.[/yellow]")
            return

        if len(entries) > n:
            console.print(f"[dim]... ({len(entries) - n} earlier entries skipped)[/dim]")
            entries = entries[-n:]

        for entry in entries:
            ts = entry.get("timestamp", "")
            action = entry.get("action", "")
            agent = entry.get("agent", "")
            tid = entry.get("task_id", "")[:8] if entry.get("task_id") else ""
            dur = entry.get("duration_ms")
            dur_str = f" ({dur:.0f}ms)" if dur else ""

            console.print(
                f"[dim]{ts}[/dim] [{_action_color(action)}]{action}[/{_action_color(action)}]"
                f" {f'[cyan]{tid}[/cyan]' if tid else ''}"
                f" {f'[green]{agent}[/green]' if agent else ''}"
                f"{dur_str}"
            )

    def _show_plain_tail(self, path: Path, n: int):
        """Print the last N lines of a plain text file."""
        console.print(f"[dim]Log file: {path}[/dim]\n")
        with open(path) as fh:
            all_lines = fh.readlines()
        if not all_lines:
            console.print("[yellow]Log file is empty.[/yellow]")
            return
        if len(all_lines) > n:
            console.print(f"[dim]... ({len(all_lines) - n} earlier lines skipped)[/dim]")
            all_lines = all_lines[-n:]
        sys.stdout.write("".join(all_lines))
        sys.stdout.flush()

    def _follow_file_raw(self, path: Path, initial_lines: int):
        """Follow a plain text file (like tail -f). No task-status polling."""
        shown_bytes = 0
        while True:
            if path.exists():
                content = path.read_text()
                if len(content) > shown_bytes:
                    new_content = content[shown_bytes:]
                    if shown_bytes == 0:
                        all_lines = new_content.splitlines(keepends=True)
                        if len(all_lines) > initial_lines:
                            console.print(f"[dim]... ({len(all_lines) - initial_lines} lines skipped)[/dim]")
                            new_content = "".join(all_lines[-initial_lines:])
                    sys.stdout.write(new_content)
                    sys.stdout.flush()
                    shown_bytes = len(content)
            time.sleep(0.5)

    # ------------------------------------------------------------------
    # stop
    # ------------------------------------------------------------------

    def stop(self, task_id: str, force: bool = False):
        """Stop a running task.

        Kills the task's tmux session, or falls back to killing the
        subprocess by PID.

        Examples:
            odin stop a1b2             Stop a specific task
            odin stop a1b2 --force     Force kill (SIGKILL)

        Args:
            task_id: Task ID or prefix to stop.
            force: Send SIGKILL instead of SIGTERM.
        """
        sig = signal.SIGKILL if force else signal.SIGTERM
        sig_name = "SIGKILL" if force else "SIGTERM"

        full_id = self._resolve_id(task_id)
        mgr = self._get_task_manager()
        task = mgr.get_task(full_id)
        if not task:
            console.print(f"[red]Task not found: {task_id}[/red]")
            return

        # Try killing tmux session first
        sess = tmux.session_name(full_id)
        tmux_killed = subprocess.run(
            ["tmux", "kill-session", "-t", sess],
            capture_output=True,
        ).returncode == 0

        if tmux_killed:
            console.print(
                f"[green]Killed tmux session {sess} for task {full_id[:8]}.[/green]"
            )
            return

        # Fallback: kill subprocess by PID
        pid = task.metadata.get("subprocess_pid") if task.metadata else None
        if not pid:
            console.print(f"[yellow]No tmux session or subprocess PID for task {full_id[:8]}.[/yellow]")
            return

        try:
            os.kill(pid, sig)
            console.print(
                f"[green]Sent {sig_name} to task {full_id[:8]} (PID {pid}).[/green]"
            )
        except ProcessLookupError:
            console.print(
                f"[yellow]Process {pid} already terminated.[/yellow]"
            )

    # ------------------------------------------------------------------
    # watch
    # ------------------------------------------------------------------

    def watch(self, interval: int = 2, spec: Optional[str] = None):
        """Auto-refreshing status dashboard.

        Clears the screen and re-renders 'odin status' every N seconds.
        Exit with Ctrl+C.

        Examples:
            odin watch
            odin watch --interval 5
            odin watch --spec sp_a1b2

        Args:
            interval: Refresh interval in seconds (default 2).
            spec: Optional spec ID to filter by.
        """
        try:
            while True:
                # Clear screen
                sys.stdout.write("\033[2J\033[H")
                sys.stdout.flush()
                self.status(spec=spec)
                console.print(f"\n[dim]Refreshing every {interval}s. Ctrl+C to exit.[/dim]")
                time.sleep(interval)
        except KeyboardInterrupt:
            console.print("\n[dim]Stopped.[/dim]")

    # ------------------------------------------------------------------
    # costs
    # ------------------------------------------------------------------

    def costs(self, spec: Optional[str] = None):
        """Show cost tracking summaries for executed tasks.

        Displays duration, task count, invocations by agent, and token
        usage per spec.

        Examples:
            odin costs                  Show all specs
            odin costs --spec sp_a1b2   Show costs for one spec

        Args:
            spec: Optional spec ID or prefix to filter by.
        """
        cost_store = self._get_cost_store()

        if spec:
            spec_store = self._get_spec_store()
            resolved = spec_store.resolve_spec_id(spec) or spec
            summaries = [cost_store.summarize_spec(resolved)]
        else:
            summaries = cost_store.summarize_all()

        if not summaries or all(s.task_count == 0 for s in summaries):
            console.print("[yellow]No cost data recorded yet.[/yellow]")
            console.print("[dim]Cost data is recorded automatically during 'odin exec'.[/dim]")
            return

        table = Table(title="Cost Tracking")
        table.add_column("Spec", style="cyan", no_wrap=True)
        table.add_column("Tasks", justify="right")
        table.add_column("Duration", justify="right")
        table.add_column("Tokens", justify="right")
        table.add_column("Agents", style="green")
        table.add_column("Tokens by Agent", style="dim")
        table.add_column("Time Range", style="dim")

        for s in summaries:
            if s.task_count == 0:
                continue
            dur_str = f"{s.total_duration_ms / 1000:.1f}s" if s.total_duration_ms else "-"
            tok_str = f"{s.total_tokens:,}" if s.total_tokens else "n/a"
            agents_str = ", ".join(
                f"{name}({count})" for name, count in sorted(s.invocations_by_agent.items())
            )
            tokens_agent_str = ", ".join(
                f"{name}:{count:,}" for name, count in sorted(s.tokens_by_agent.items())
            ) if s.tokens_by_agent else "-"
            time_range = ""
            if s.first_recorded and s.last_recorded:
                time_range = (
                    f"{s.first_recorded.strftime('%H:%M:%S')}"
                    f" - {s.last_recorded.strftime('%H:%M:%S')}"
                )
            table.add_row(
                s.spec_id,
                str(s.task_count),
                dur_str,
                tok_str,
                agents_str,
                tokens_agent_str,
                time_range,
            )

        console.print(table)

    # ------------------------------------------------------------------
    # show
    # ------------------------------------------------------------------

    def show(self, task_id: str):
        """Show full details of a task.

        Displays description, result, comments, metadata, and status.

        Examples:
            odin show a1b2
            odin show 8f104a78f19b

        Args:
            task_id: Task ID or unique prefix.
        """
        full_id = self._resolve_id(task_id)
        cfg = self._get_config()
        mgr = self._get_task_manager()
        task = mgr.get_task(full_id)

        if not task:
            console.print(f"[red]Task not found: {task_id}[/red]")
            return

        status_colors = {
            "backlog": "dim",
            "todo": "blue",
            "in_progress": "cyan",
            "executing": "bright_green",
            "review": "yellow",
            "testing": "magenta",
            "done": "green",
            "failed": "red",
        }
        color = status_colors.get(task.status.value, "white")

        console.print(f"\n[bold]Task {task.id}[/bold]")
        console.print(f"  Title:   {task.title}")
        console.print(f"  Status:  [{color}]{task.status.value}[/{color}]")
        console.print(f"  Agent:   {task.assigned_agent or '-'}")
        model = task.metadata.get("selected_model", "-") if task.metadata else "-"
        console.print(f"  Model:   {model}")
        if task.spec_id:
            console.print(f"  Spec:    [cyan]{task.spec_id}[/cyan]")
        console.print(f"  Created: {task.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
        console.print(f"  Updated: {task.updated_at.strftime('%Y-%m-%d %H:%M:%S')}")

        # Cost info from cost tracking
        try:
            cost_store = self._get_cost_store()
            if task.spec_id:
                records = cost_store.load_by_spec(task.spec_id)
            else:
                records = cost_store.load_all()
            task_records = [r for r in records if r.task_id == task.id]
            if task_records:
                rec = task_records[-1]  # latest record for this task
                dur_str = f"{rec.duration_ms / 1000:.1f}s" if rec.duration_ms else "-"
                tok_str = f"{rec.total_tokens:,}" if rec.total_tokens else "n/a"
                console.print(f"  Duration: {dur_str}")
                console.print(f"  Tokens: {tok_str}")
        except Exception:
            pass

        # Reasoning
        if task.metadata and task.metadata.get("reasoning"):
            console.print(f"  Reason:  [dim]{task.metadata['reasoning']}[/dim]")

        # Capabilities
        if task.metadata and task.metadata.get("required_capabilities"):
            caps = ", ".join(task.metadata["required_capabilities"])
            console.print(f"  Caps:    [dim]{caps}[/dim]")

        # Dependencies (with full context)
        if task.depends_on:
            console.print(f"\n[bold]Depends On ({len(task.depends_on)}):[/bold]")
            for dep_id in task.depends_on:
                dep_task = mgr.get_task(dep_id)
                if dep_task:
                    dep_color = status_colors.get(dep_task.status.value, "white")
                    dep_model = dep_task.metadata.get("selected_model", "-") if dep_task.metadata else "-"
                    console.print(
                        f"  [cyan]{dep_id[:8]}[/cyan] "
                        f"[{dep_color}]{dep_task.status.value:12}[/{dep_color}] "
                        f"[green]{dep_task.assigned_agent or '-':8}[/green] "
                        f"[bright_green]{dep_model}[/bright_green]"
                    )
                    console.print(f"           {dep_task.title}")
                else:
                    console.print(f"  [cyan]{dep_id[:8]}[/cyan] [dim]not found[/dim]")

        # Reverse dependencies (blocks) with full context
        all_tasks = mgr.list_tasks()
        blocks = [t for t in all_tasks if task.id in t.depends_on]
        if blocks:
            console.print(f"\n[bold]Blocks ({len(blocks)}):[/bold]")
            for bt in blocks:
                bt_color = status_colors.get(bt.status.value, "white")
                bt_model = bt.metadata.get("selected_model", "-") if bt.metadata else "-"
                console.print(
                    f"  [cyan]{bt.id[:8]}[/cyan] "
                    f"[{bt_color}]{bt.status.value:12}[/{bt_color}] "
                    f"[green]{bt.assigned_agent or '-':8}[/green] "
                    f"[bright_green]{bt_model}[/bright_green]"
                )
                console.print(f"           {bt.title}")

        # Description
        console.print(f"\n[bold]Description:[/bold]\n{task.description}")

        # Comments
        if task.comments:
            console.print(f"\n[bold]Comments ({len(task.comments)}):[/bold]")
            for c in task.comments:
                console.print(
                    f"  [{c.created_at.strftime('%H:%M:%S')}] "
                    f"[bold]{c.author}[/bold]: {c.content}"
                )

        # Raw metadata (excluding already-displayed fields)
        if task.metadata:
            extra = {
                k: v for k, v in task.metadata.items()
                if k not in ("reasoning", "required_capabilities", "suggested_agent", "selected_model", "quota_snapshot")
            }
            if extra:
                console.print(f"\n[bold]Metadata:[/bold]\n{json.dumps(extra, indent=4)}")

    # ------------------------------------------------------------------
    # run (convenience all-in-one)
    # ------------------------------------------------------------------

    def run(self, spec_file: Optional[str] = None, prompt: Optional[str] = None):
        """Plan tasks from a spec and dispatch for execution.

        TaskIt backend: plan + move assigned tasks to IN_PROGRESS (Celery
        DAG executor picks them up and runs in dependency order).

        Local backend: plan only, prints guidance to use ``odin exec <id>``.

        Examples:
            odin run specs/poem_spec.md
            odin run --prompt "Write a haiku about technology"

        Args:
            spec_file: Path to a markdown spec file.
            prompt: Inline prompt string (alternative to spec_file).
        """
        if not spec_file and not prompt:
            console.print("[red]Provide either a spec file or --prompt.[/red]")
            console.print('[dim]Usage: odin run <spec_file>  or  odin run --prompt "..."[/dim]')
            return

        if spec_file:
            path = Path(spec_file)
            if not path.exists():
                console.print(f"[red]Spec file not found: {spec_file}[/red]")
                return
            spec = path.read_text()
            console.print(f"[dim]Loaded spec from {spec_file}[/dim]")
        else:
            spec = prompt

        cfg = self._get_config()
        orch = Orchestrator(cfg)

        console.print("[bold]Planning...[/bold]")
        with console.status("[bold green]Planning..."):
            sid, tasks = asyncio.run(orch.plan(spec, spec_file=spec_file))

        console.print(
            f"\n[bold green]Plan created![/bold green] "
            f"Spec [cyan]{sid}[/cyan] with {len(tasks)} tasks."
        )

        if cfg.board_backend == "taskit":
            # TaskIt + Celery: bulk-move to IN_PROGRESS for DAG executor
            mgr = self._get_task_manager()
            moved = 0
            for t in tasks:
                if t.assigned_agent:
                    mgr.update_status(t.id, TaskStatus.IN_PROGRESS)
                    moved += 1
            console.print(
                f"[bold green]Queued {moved} tasks.[/bold green] "
                "DAG executor will run them in dependency order."
            )
            console.print("[dim]Use 'odin watch' to monitor progress.[/dim]")
        else:
            # Local backend: plan only, user runs tasks individually
            console.print(
                "\n[dim]Use [bold]odin exec <task_id>[/bold] to run tasks individually.[/dim]"
            )
            console.print("[dim]Use [bold]odin status[/bold] to see task IDs.[/dim]")

    # ------------------------------------------------------------------
    # status (enhanced with spec column and filters)
    # ------------------------------------------------------------------

    def status(
        self,
        spec: Optional[str] = None,
        agent: Optional[str] = None,
        status: Optional[str] = None,
    ):
        """Show current tasks and their statuses.

        Displays a table of all tasks with IDs, status, agent, spec tag,
        result preview, and a summary line.

        Examples:
            odin status
            odin status --spec sp_a1b2
            odin status --agent claude
            odin status --status failed

        Args:
            spec: Filter by spec ID (prefix match).
            agent: Filter by agent name.
            status: Filter by task status (backlog, todo, in_progress, executing, review, testing, done, failed).
        """
        cfg = self._get_config()
        mgr = self._get_task_manager()
        spec_store = self._get_spec_store()

        # Resolve spec prefix
        filter_spec_id = None
        if spec:
            filter_spec_id = spec_store.resolve_spec_id(spec) or spec

        # Apply filters
        filter_status = None
        if status:
            try:
                filter_status = TaskStatus(status)
            except ValueError:
                console.print(f"[red]Unknown status: {status}[/red]")
                console.print(f"[dim]Valid: backlog, todo, in_progress, executing, review, testing, done, failed[/dim]")
                return

        tasks = mgr.list_tasks(
            status=filter_status,
            agent=agent,
            spec_id=filter_spec_id,
        )

        if not tasks:
            console.print("[yellow]No tasks found.[/yellow]")
            return

        # Build spec title cache for short tags
        spec_tags = {}
        for s in spec_store.load_all():
            spec_tags[s.id] = spec_short_tag(s.title)

        table = Table(title="Odin Tasks")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Title")
        table.add_column("Status", style="bold")
        table.add_column("Agent", style="green")
        table.add_column("Spec", style="magenta", no_wrap=True)
        table.add_column("Model", style="bright_green")
        table.add_column("Deps", style="magenta", no_wrap=True)
        table.add_column("Elapsed", style="cyan", no_wrap=True)
        table.add_column("Updated")

        status_colors = {
            "backlog": "dim",
            "todo": "blue",
            "in_progress": "cyan",
            "executing": "bright_green",
            "review": "yellow",
            "testing": "magenta",
            "done": "green",
            "failed": "red",
        }

        now = time.time()
        for t in tasks:
            color = status_colors.get(t.status.value, "white")
            model = t.metadata.get("selected_model", "-") if t.metadata else "-"
            deps_str = ", ".join(d[:8] for d in t.depends_on) if t.depends_on else "-"
            tag = spec_tags.get(t.spec_id, "-") if t.spec_id else "-"

            # Elapsed time for IN_PROGRESS/EXECUTING tasks
            elapsed_str = "-"
            if t.status in (TaskStatus.IN_PROGRESS, TaskStatus.EXECUTING) and t.metadata:
                started_at = t.metadata.get("started_at")
                if started_at:
                    elapsed_str = _format_elapsed(now - started_at)

            table.add_row(
                t.id,
                t.title[:40],
                f"[{color}]{t.status.value}[/{color}]",
                t.assigned_agent or "-",
                tag,
                model,
                deps_str,
                elapsed_str,
                t.updated_at.strftime("%H:%M:%S"),
            )

        console.print(table)

        # Summary line
        counts = {}
        for t in tasks:
            counts[t.status.value] = counts.get(t.status.value, 0) + 1
        parts = []
        for s in ["done", "failed", "executing", "in_progress", "review", "testing", "todo", "backlog"]:
            if s in counts:
                c = status_colors.get(s, "white")
                parts.append(f"[{c}]{counts[s]} {s}[/{c}]")
        console.print(f"\n  {', '.join(parts)}  ({len(tasks)} total)")

    def tasks(self):
        """List all tasks (alias for status).

        Example:
            odin tasks
        """
        self.status()

    # ------------------------------------------------------------------
    # specs — list all specs with derived status
    # ------------------------------------------------------------------

    def specs(self):
        """List all specs with derived status.

        Shows one row per spec, with status derived from its tasks.

        Example:
            odin specs
        """
        cfg = self._get_config()
        mgr = self._get_task_manager()
        spec_store = self._get_spec_store()

        all_specs = spec_store.load_all()
        if not all_specs:
            console.print("[yellow]No specs found. Run 'odin plan' first.[/yellow]")
            return

        table = Table(title="Odin Specs")
        table.add_column("Spec", style="cyan", no_wrap=True)
        table.add_column("Title")
        table.add_column("Status", style="bold")
        table.add_column("Tasks")
        table.add_column("Created")

        status_colors = {
            "planned": "blue",
            "active": "cyan",
            "done": "green",
            "blocked": "red",
            "partial": "yellow",
            "draft": "dim",
            "abandoned": "dim red",
            "empty": "dim",
        }

        for s in all_specs:
            spec_tasks = mgr.list_tasks(spec_id=s.id)
            derived = derive_spec_status(spec_tasks, s.abandoned)
            color = status_colors.get(derived, "white")

            # Task summary
            counts = {}
            for t in spec_tasks:
                counts[t.status.value] = counts.get(t.status.value, 0) + 1
            parts = []
            for st_name in ["done", "failed", "in_progress", "review", "testing", "todo", "backlog"]:
                if st_name in counts:
                    parts.append(f"{counts[st_name]} {st_name}")
            task_summary = ", ".join(parts) if parts else "no tasks"

            table.add_row(
                s.id,
                s.title[:40],
                f"[{color}]{derived}[/{color}]",
                task_summary,
                s.created_at.strftime("%Y-%m-%d %H:%M"),
            )

        console.print(table)

    # ------------------------------------------------------------------
    # spec — subcommands (show, abandon)
    # ------------------------------------------------------------------

    def spec(self, action: str, spec_id: Optional[str] = None):
        """Spec management subcommands.

        Examples:
            odin spec show sp_a1b2     Show spec content and its tasks
            odin spec abandon sp_a1b2  Mark spec as abandoned

        Args:
            action: One of 'show' or 'abandon'.
            spec_id: Spec ID or prefix.
        """
        if action == "show":
            if not spec_id:
                console.print("[red]Usage: odin spec show <spec_id>[/red]")
                return
            self._spec_show(spec_id)
        elif action == "abandon":
            if not spec_id:
                console.print("[red]Usage: odin spec abandon <spec_id>[/red]")
                return
            self._spec_abandon(spec_id)
        else:
            console.print(f"[red]Unknown spec action: {action}[/red]")
            console.print("[dim]Available: show, abandon[/dim]")

    def _spec_show(self, spec_id: str):
        """Show spec details and its tasks."""
        cfg = self._get_config()
        mgr = self._get_task_manager()
        spec_store = self._get_spec_store()

        resolved = spec_store.resolve_spec_id(spec_id) or spec_id
        spec_obj = spec_store.load(resolved)
        if not spec_obj:
            console.print(f"[red]Spec not found: {spec_id}[/red]")
            return

        spec_tasks = mgr.list_tasks(spec_id=resolved)
        derived = derive_spec_status(spec_tasks, spec_obj.abandoned)

        status_colors = {
            "planned": "blue",
            "active": "cyan",
            "done": "green",
            "blocked": "red",
            "partial": "yellow",
            "draft": "dim",
            "abandoned": "dim red",
            "empty": "dim",
        }
        color = status_colors.get(derived, "white")

        console.print(f"\n[bold]Spec {spec_obj.id}[/bold] — \"{spec_obj.title}\"")
        console.print(f"  Source:  {spec_obj.source}")
        console.print(f"  Planned: {spec_obj.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
        console.print(f"  Status:  [{color}]{derived}[/{color}]")

        # Spec content preview
        content_preview = spec_obj.content[:500]
        if len(spec_obj.content) > 500:
            content_preview += "..."
        console.print(f"\n[bold]Original Spec:[/bold]\n  {content_preview}")

        # Tasks
        if spec_tasks:
            console.print(f"\n[bold]Tasks ({len(spec_tasks)}):[/bold]")

            task_status_colors = {
                "pending": "yellow",
                "assigned": "blue",
                "in_progress": "cyan",
                "completed": "green",
                "failed": "red",
            }

            for t in spec_tasks:
                tc = task_status_colors.get(t.status.value, "white")
                deps_str = f"  deps: {', '.join(d[:4] for d in t.depends_on)}" if t.depends_on else ""
                console.print(
                    f"  [cyan]{t.id[:8]}[/cyan]  {t.title:30}  "
                    f"[green]{t.assigned_agent or '-':8}[/green]  "
                    f"[{tc}]{t.status.value:12}[/{tc}]{deps_str}"
                )
        else:
            console.print("\n[dim]No tasks.[/dim]")

    def _spec_abandon(self, spec_id: str):
        """Mark a spec as abandoned."""
        spec_store = self._get_spec_store()

        resolved = spec_store.resolve_spec_id(spec_id) or spec_id
        spec_obj = spec_store.set_abandoned(resolved)
        if not spec_obj:
            console.print(f"[red]Spec not found: {spec_id}[/red]")
            return

        console.print(
            f"[yellow]Abandoned[/yellow] spec [cyan]{resolved}[/cyan] — \"{spec_obj.title}\""
        )
        console.print("[dim]Tasks are preserved as historical evidence.[/dim]")

    # ------------------------------------------------------------------
    # label
    # ------------------------------------------------------------------

    def label(self, action: str, name: Optional[str] = None, color: Optional[str] = None):
        """Label management subcommands.

        Examples:
            odin label list                 List all labels
            odin label create Bug red       Create a label

        Args:
            action: One of 'list' or 'create'.
            name: Label name (required for create).
            color: Label color (required for create, e.g. 'red', '#ff0000').
        """
        if action == "list":
            self._label_list()
        elif action == "create":
            if not name or not color:
                console.print("[red]Usage: odin label create <name> <color>[/red]")
                return
            self._label_create(name, color)
        else:
            console.print(f"[red]Unknown label action: {action}[/red]")
            console.print("[dim]Available: list, create[/dim]")

    def _label_list(self):
        """List all labels."""
        cfg = self._get_config()
        mgr = self._get_task_manager()
        backend = mgr._backend
        if not backend:
            console.print("[yellow]Labels require a board backend (e.g. taskit).[/yellow]")
            return
        labels = backend.list_labels()
        if not labels:
            console.print("[yellow]No labels found.[/yellow]")
            return
        table = Table(title="Labels")
        table.add_column("ID", style="cyan")
        table.add_column("Name")
        table.add_column("Color", style="green")
        for lbl in labels:
            table.add_row(str(lbl.get("id", "")), lbl.get("name", ""), lbl.get("color", ""))
        console.print(table)

    def _label_create(self, name: str, color: str):
        """Create a new label."""
        cfg = self._get_config()
        mgr = self._get_task_manager()
        backend = mgr._backend
        if not backend:
            console.print("[yellow]Labels require a board backend (e.g. taskit).[/yellow]")
            return
        result = backend.create_label(name, color)
        console.print(f"[green]Created label[/green] [cyan]{result.get('name')}[/cyan] ({result.get('color')})")

    # ------------------------------------------------------------------
    # logs
    # ------------------------------------------------------------------

    def logs(self, target: Optional[str] = None, follow: bool = False,
             n: int = 50, board: Optional[int] = None, f: bool = False):
        """Show logs from the latest orchestration run.

        Consolidates run logs, debug logs, and live task output into a
        single command.  Replaces the old ``odin tail`` (which still works
        as a deprecated alias).

        Examples:
            odin logs                      Last 50 lines of latest run log
            odin logs a1b2                 Run log filtered to task a1b2
            odin logs debug                Last 50 lines of odin_detail.log
            odin logs -f                   Follow all running tasks
            odin logs a1b2 -f              Follow a specific task's output
            odin logs debug -f             Tail odin_detail.log
            odin logs -n 100               Show last 100 lines
            odin logs -b 5                 Use project registered as board 5
            odin logs debug -b 5 -f        Full combo

        Args:
            target: What to show — a task ID prefix, ``"debug"``, or None
                    (latest run log).
            follow: Stream new output (like ``tail -f``).  Short flag: -f.
            n: Number of lines to show (default 50).
            board: Resolve project path from ``~/.odin/boards.json`` by
                   board ID, so you can read logs without cd-ing into the
                   project directory.
            f: Short alias for --follow.
        """
        follow = follow or f

        # Resolve log directory -----------------------------------------
        if board is not None:
            from odin.board_registry import resolve_board_path
            project_path = resolve_board_path(board)
            if project_path is None:
                console.print(f"[red]Board {board} not found in ~/.odin/boards.json.[/red]")
                console.print("[dim]Register it with: odin init --board-id <id>[/dim]")
                return
            log_dir = project_path / ".odin" / "logs"
        else:
            cfg = self._get_config()
            log_dir = Path(cfg.log_dir)

        if not log_dir.exists():
            console.print("[yellow]No logs directory found.[/yellow]")
            return

        # Dispatch -------------------------------------------------------
        if target == "debug":
            debug_log = log_dir / "odin_detail.log"
            if not debug_log.exists():
                console.print("[yellow]No odin_detail.log found.[/yellow]")
                return
            if follow:
                console.print(f"[dim]Following {debug_log}  (Ctrl+C to stop)[/dim]\n")
                try:
                    self._follow_file_raw(debug_log, n)
                except KeyboardInterrupt:
                    console.print("\n[dim]Stopped.[/dim]")
            else:
                self._show_plain_tail(debug_log, n)

        elif target is not None:
            # target is a task_id prefix
            if follow:
                if board is not None:
                    console.print(
                        "[yellow]Cannot follow a task with --board — task status polling "
                        "requires running from the project directory.[/yellow]"
                    )
                    return
                full_id = self._resolve_id(target)
                out_path = log_dir / f"task_{full_id}.out"
                mgr = self._get_task_manager()
                if not out_path.exists():
                    console.print(f"[yellow]No output file yet for task {full_id[:8]}.[/yellow]")
                    console.print("[dim]Task may not have started. Waiting...[/dim]")
                try:
                    self._tail_file(out_path, full_id, mgr, n)
                except KeyboardInterrupt:
                    console.print("\n[dim]Stopped.[/dim]")
            else:
                self._show_run_log(log_dir, task_filter=target, n=n)

        else:
            # No target
            if follow:
                if board is not None:
                    console.print(
                        "[yellow]Cannot follow tasks with --board — task status polling "
                        "requires running from the project directory.[/yellow]"
                    )
                    return
                mgr = self._get_task_manager()
                agent_colors = ["cyan", "green", "yellow", "magenta", "blue", "red"]
                try:
                    self._tail_all(mgr, log_dir, n, agent_colors)
                except KeyboardInterrupt:
                    console.print("\n[dim]Stopped.[/dim]")
            else:
                self._show_run_log(log_dir, task_filter=None, n=n)

    # ------------------------------------------------------------------
    # config
    # ------------------------------------------------------------------

    def config(self):
        """Show loaded configuration and agent settings.

        Example:
            odin config
        """
        cfg = self._get_config()
        source = cfg.config_source or "unknown"
        console.print(f"[dim]Config loaded from:[/dim] {source}")
        console.print(f"[dim]Base agent:[/dim] {cfg.base_agent}")
        console.print(f"[dim]Task storage:[/dim] {cfg.task_storage}")
        console.print(f"[dim]Log dir:[/dim] {cfg.log_dir}\n")

        table = Table(title="Agent Configuration")
        table.add_column("Agent", style="cyan")
        table.add_column("Enabled", style="green")
        table.add_column("CLI/API")
        table.add_column("Default Model", style="bright_green")
        table.add_column("Premium Model", style="bright_green")
        table.add_column("Capabilities")
        table.add_column("Cost Tier")

        for name, agent in cfg.agents.items():
            cli_api = agent.cli_command or ("API" if agent.api_key else "-")
            caps = ", ".join(agent.capabilities) if agent.capabilities else "-"
            table.add_row(
                name,
                "yes" if agent.enabled else "[dim]no[/dim]",
                cli_api,
                agent.default_model or "-",
                agent.premium_model or "-",
                caps,
                agent.cost_tier.value,
            )

        console.print(table)

        if cfg.banned_models:
            console.print(
                f"\n[dim]Banned models:[/dim] [red]{', '.join(cfg.banned_models)}[/red]"
            )

        # Model routing priority
        if cfg.model_routing:
            console.print("\n[bold]Model Routing Priority[/bold] (highest first):")
            for i, route in enumerate(cfg.model_routing, 1):
                agent_cfg = cfg.agents.get(route.agent)
                if agent_cfg and agent_cfg.enabled:
                    status = "[green]enabled[/green]"
                else:
                    status = "[dim]disabled[/dim]"
                console.print(
                    f"  {i:2}. [cyan]{route.agent}[/cyan] / "
                    f"[bright_green]{route.model}[/bright_green]  {status}"
                )

    # ------------------------------------------------------------------
    # doctor
    # ------------------------------------------------------------------

    def doctor(self):
        """Run sanity checks on odin setup.

        Checks config loading, .odin/ directory, agent CLI availability,
        and taskit backend connectivity (if configured).

        Example:
            odin doctor
        """
        console.print("[bold]Odin Doctor[/bold]\n")
        all_ok = True

        # 1. Config
        try:
            cfg = self._get_config()
            console.print(f"  [green]\u2713[/green] Config loaded from: {cfg.config_source}")
        except Exception as e:
            console.print(f"  [red]\u2717[/red] Config failed: {e}")
            all_ok = False
            return

        # 2. .odin directory
        odin_dir = Path(cfg.task_storage).parent
        if odin_dir.exists():
            console.print(f"  [green]\u2713[/green] .odin/ directory exists at {odin_dir}")
        else:
            console.print(f"  [red]\u2717[/red] .odin/ directory not found at {odin_dir}")
            console.print("    [dim]Run 'odin init' to create it.[/dim]")
            all_ok = False

        # 3. Base agent
        console.print(f"  [dim]  Base agent: {cfg.base_agent}[/dim]")

        # 4. Agent availability
        console.print("\n[bold]Agents:[/bold]")
        from odin.harnesses import get_harness
        for name, agent_cfg in cfg.agents.items():
            if not agent_cfg.enabled:
                console.print(f"  [dim]-[/dim] {name}: [dim]disabled[/dim]")
                continue
            try:
                h = get_harness(name, agent_cfg)
                available = asyncio.run(h.is_available())
                if available:
                    label = agent_cfg.cli_command or "API"
                    console.print(f"  [green]\u2713[/green] {name}: available ({label})")
                else:
                    if agent_cfg.cli_command:
                        console.print(f"  [yellow]\u2717[/yellow] {name}: not available (CLI '{agent_cfg.cli_command}' not found)")
                    else:
                        console.print(f"  [yellow]\u2717[/yellow] {name}: not available (no API key configured)")
            except Exception as e:
                console.print(f"  [red]\u2717[/red] {name}: error — {e}")

        # 5. Board backend
        console.print(f"\n[bold]Board Backend:[/bold] {cfg.board_backend}")
        if cfg.board_backend == "taskit":
            if not cfg.taskit:
                console.print(f"  [red]\u2717[/red] board_backend is 'taskit' but no [taskit] config section found")
                all_ok = False
            else:
                console.print(f"  [dim]  URL: {cfg.taskit.base_url}[/dim]")
                console.print(f"  [dim]  Board ID: {cfg.taskit.board_id}[/dim]")
                try:
                    from odin.backends.taskit import TaskItBackend
                    backend = TaskItBackend(
                        base_url=cfg.taskit.base_url,
                        board_id=cfg.taskit.board_id,
                        created_by=cfg.taskit.created_by,
                    )
                    info = backend.ping()
                    if info["ok"]:
                        console.print(f"  [green]\u2713[/green] TaskIt connected")
                        console.print(f"    Board exists: {info.get('board_exists', '?')}")
                        console.print(f"    Tasks on board: {info.get('task_count', '?')}")
                        console.print(f"    Specs on board: {info.get('spec_count', '?')}")
                    else:
                        err = info.get("error", "board not found")
                        console.print(f"  [red]\u2717[/red] TaskIt check failed: {err}")
                        if not info.get("board_exists", True):
                            console.print(
                                f"    [yellow]Board {cfg.taskit.board_id} does not exist. "
                                f"Create it at {cfg.taskit.base_url} first.[/yellow]"
                            )
                        all_ok = False
                except Exception as e:
                    console.print(f"  [red]\u2717[/red] TaskIt connection error: {e}")
                    all_ok = False
        elif cfg.board_backend == "local":
            tasks_dir = Path(cfg.task_storage)
            if tasks_dir.exists():
                task_count = len(list(tasks_dir.glob("task_*.json")))
                console.print(f"  [green]\u2713[/green] Local storage at {tasks_dir} ({task_count} tasks)")
            else:
                console.print(f"  [yellow]-[/yellow] Local storage dir does not exist yet (will be created on first plan)")

        # Summary
        if all_ok:
            console.print("\n[bold green]All checks passed.[/bold green]")
        else:
            console.print("\n[bold yellow]Some checks failed. See above for details.[/bold yellow]")

    # ------------------------------------------------------------------
    # guide
    # ------------------------------------------------------------------

    def guide(self):
        """Show the sample workflow walkthrough.

        Prints the staged workflow guide explaining how to use odin
        step by step: plan, review, assign, exec.

        Example:
            odin guide
        """
        guide_path = Path(__file__).parent.parent.parent / "docs" / "sample_flow.md"
        if not guide_path.exists():
            # Fallback: search relative to package
            for candidate in [
                Path(__file__).resolve().parent.parent.parent / "docs" / "sample_flow.md",
                Path.cwd() / "odin" / "docs" / "sample_flow.md",
                Path.cwd() / "docs" / "sample_flow.md",
            ]:
                if candidate.exists():
                    guide_path = candidate
                    break

        if guide_path.exists():
            from rich.markdown import Markdown
            md = Markdown(guide_path.read_text())
            console.print(md)
        else:
            console.print("[yellow]Guide file not found.[/yellow]")
            console.print("[dim]Expected at: odin/docs/sample_flow.md[/dim]")

    # ------------------------------------------------------------------
    # mcp_config
    # ------------------------------------------------------------------

    def mcp_config(self, task_id: Optional[str] = None):
        """Generate MCP config files for all agent CLIs.

        Creates per-CLI config files so each agent CLI auto-discovers TaskIt
        MCP tools. Auth uses the same .env vars as odin (ODIN_ADMIN_USER,
        ODIN_ADMIN_PASSWORD) — no hardcoded tokens needed.

        Config files generated:
            .mcp.json              Claude Code
            .gemini/settings.json  Gemini CLI
            .qwen/settings.json    Qwen CLI
            .codex/config.toml     Codex CLI (TOML format)
            .kilocode/mcp.json     Kilo Code
            opencode.json          OpenCode

        Examples:
            odin mcp_config                 Generate all configs (no task scope)
            odin mcp_config 42              Scope MCP tools to task 42

        After generating, run any MCP-compatible CLI from this directory:
            gemini                          # auto-discovers .gemini/settings.json
            claude                          # auto-discovers .mcp.json
            qwen                            # auto-discovers .qwen/settings.json

        Args:
            task_id: Optional task ID to scope tools to. If set, TASKIT_TASK_ID
                is baked into the config so tools operate on this task automatically.
        """
        cfg = self._get_config()

        if not cfg.taskit:
            console.print("[red]TaskIt backend not configured.[/red]")
            raise SystemExit(1)

        # Resolve task ID if provided
        resolved_task_id = ""
        if task_id:
            resolved_task_id = self._resolve_id(task_id)
            console.print(f"Scoped to task [bold]{resolved_task_id}[/bold]")

        env_block = {"TASKIT_URL": cfg.taskit.base_url}
        if resolved_task_id:
            env_block["TASKIT_TASK_ID"] = str(resolved_task_id)

        created = _generate_all_mcp_configs(Path.cwd(), env_block)
        for path in created:
            console.print(f"[green]Generated[/green] {path}")

        console.print(f"\nRun any MCP-compatible CLI from this directory:")
        console.print(f"  [bold]claude[/bold]     # auto-discovers .mcp.json")
        console.print(f"  [bold]gemini[/bold]     # auto-discovers .gemini/settings.json")
        console.print(f"  [bold]qwen[/bold]       # auto-discovers .qwen/settings.json")
        console.print(f"  [bold]codex[/bold]      # auto-discovers .codex/config.toml")
        console.print(f"\n[dim]Auth uses ODIN_ADMIN_USER/PASSWORD from .env (same as odin).[/dim]")
        if not resolved_task_id:
            console.print(f"[dim]No task_id set — pass task_id to each tool call.[/dim]")

    # ------------------------------------------------------------------
    # test
    # ------------------------------------------------------------------

    def test(self, suite: Optional[str] = None):
        """Run the odin test suite.

        Runs integration tests that exercise real agent CLIs.

        Examples:
            odin test             Run all tests
            odin test quick       Fast: harness availability only
            odin test plan        Test planning (decomposition + task creation)
            odin test e2e         Full end-to-end pipeline
            odin test staged      All staged workflow tests

        Args:
            suite: Test suite to run. One of: quick, plan, e2e, staged, or
                   omit for all tests.
        """
        test_dir = Path(__file__).parent.parent.parent / "tests"
        if not test_dir.exists():
            for candidate in [
                Path(__file__).resolve().parent.parent.parent / "tests",
                Path.cwd() / "odin" / "tests",
                Path.cwd() / "tests",
            ]:
                if candidate.exists():
                    test_dir = candidate
                    break

        test_file = test_dir / "test_real.py"
        if not test_file.exists():
            console.print(f"[red]Test file not found at {test_file}[/red]")
            return

        suite_map = {
            "quick": "TestHarnessAvailability",
            "plan": "TestPlanOnly",
            "e2e": "TestFullPoemE2E",
            "staged": "TestPlanOnly or TestExecSingleTask or TestReassign",
            "decompose": "TestDecomposition",
            "single": "TestExecSingleTask",
            "reassign": "TestReassign",
            "spec": "TestSpec",
        }

        cmd = [sys.executable, "-m", "pytest", str(test_file), "-v", "-s"]

        if suite:
            if suite not in suite_map:
                console.print(f"[red]Unknown test suite: {suite}[/red]")
                available = ", ".join(suite_map.keys())
                console.print(f"[dim]Available: {available}[/dim]")
                return
            cmd.extend(["-k", suite_map[suite]])
            console.print(f"[bold]Running test suite: {suite}[/bold]")
        else:
            console.print("[bold]Running all tests...[/bold]")

        console.print(f"[dim]{' '.join(cmd)}[/dim]\n")
        result = subprocess.run(cmd)
        raise SystemExit(result.returncode)


def _generate_all_mcp_configs(
    base_dir: Path, env: dict, mcps: list[str] | None = None,
) -> list[Path]:
    """Generate MCP config files for all agent CLIs.

    Delegates to the shared formatters in ``odin.mcps.taskit_mcp.config``
    so tool names are always derived from the FastMCP server instance.
    When *mcps* includes ``"mobile"`` or ``"chrome-devtools"``, their
    server entries are merged into each config file.

    Args:
        base_dir: Directory to write config files into (usually cwd).
        env: Environment variables for taskit-mcp (TASKIT_URL, etc.).
        mcps: List of MCP server names to include (default: ``["taskit"]``).

    Returns:
        List of Paths that were created.
    """
    from odin.mcps.taskit_mcp.config import (
        MCP_CONFIG_MAP, MCP_FORMATTERS,
        server_entry as taskit_server_entry,
        tool_names as taskit_tool_names,
    )

    if mcps is None:
        mcps = ["taskit", "mobile", "chrome-devtools"]
    has_mobile = "mobile" in mcps
    has_chrome_devtools = "chrome-devtools" in mcps
    needs_merge = has_mobile or has_chrome_devtools

    created: list[Path] = []
    seen_paths: set[str] = set()

    for agent_name, rel_path in MCP_CONFIG_MAP.items():
        # Multiple agents may share the same config file (e.g. minimax + glm -> opencode.json)
        if rel_path in seen_paths:
            continue
        seen_paths.add(rel_path)

        formatter = MCP_FORMATTERS.get(agent_name)
        if not formatter:
            continue

        if needs_merge:
            content = _merge_mcp_config(
                agent_name, env, formatter,
                has_mobile=has_mobile,
                has_chrome_devtools=has_chrome_devtools,
            )
        else:
            content = formatter(env)

        p = base_dir / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content + "\n")
        created.append(p)

    return created


def _merge_mcp_config(
    agent_name: str, env: dict, formatter,
    *, has_mobile: bool = False, has_chrome_devtools: bool = False,
) -> str:
    """Merge taskit + mobile + chrome-devtools MCP entries into a single config.

    Mirrors the orchestrator's ``_generate_mcp_config`` logic but for
    init-time config generation.
    """
    from odin.mcps.taskit_mcp.config import (
        server_entry as taskit_server_entry,
        tool_names as taskit_tool_names,
    )

    # Codex uses TOML — append extra MCP sections
    if agent_name == "codex":
        base = formatter(env)
        extra_lines: list[str] = []
        if has_mobile:
            extra_lines.extend([
                "",
                "[mcp_servers.mobile]",
                'command = "npx"',
                'args = ["-y", "@mobilenext/mobile-mcp@latest"]',
            ])
        if has_chrome_devtools:
            extra_lines.extend([
                "",
                "[mcp_servers.chrome-devtools]",
                'command = "npx"',
                'args = ["-y", "chrome-devtools-mcp@latest"]',
            ])
        return base.rstrip("\n") + "\n" + "\n".join(extra_lines) + "\n"

    # OpenCode agents — merge mcp + permission dicts
    if agent_name in ("minimax", "glm"):
        mcp_servers = {**taskit_server_entry(agent_name, env)}
        permission = {t: "allow" for t in taskit_tool_names()}
        if has_mobile:
            from odin.mcps.mobile_mcp.config import (
                server_fragment as mobile_fragment,
                _opencode_permissions as mobile_opencode_permissions,
            )
            mcp_servers.update(mobile_fragment(agent_name))
            permission.update(mobile_opencode_permissions())
        if has_chrome_devtools:
            from odin.mcps.chrome_devtools_mcp.config import (
                server_fragment as cd_fragment,
                _opencode_permissions as cd_opencode_permissions,
            )
            mcp_servers.update(cd_fragment(agent_name))
            permission.update(cd_opencode_permissions())
        return json.dumps({"permission": permission, "mcp": mcp_servers}, indent=2)

    # mcpServers-based agents (claude, gemini, qwen) — merge server dicts
    servers = {**taskit_server_entry(agent_name, env)}
    if has_mobile:
        from odin.mcps.mobile_mcp.config import server_fragment as mobile_fragment
        servers.update(mobile_fragment(agent_name))
    if has_chrome_devtools:
        from odin.mcps.chrome_devtools_mcp.config import server_fragment as cd_fragment
        servers.update(cd_fragment(agent_name))
    return json.dumps({"mcpServers": servers}, indent=2)


def _generate_claude_settings(
    base_dir: Path, mcps: list[str] | None = None,
) -> Path | None:
    """Generate ``.claude/settings.local.json`` with tool permissions.

    Merges into existing file if present (preserves user customizations).
    When *mcps* includes ``"mobile"``, mobile tool permissions are included.
    Returns the path written, or None if nothing changed.
    """
    from odin.mcps.taskit_mcp.config import format_claude_settings

    settings_dir = base_dir / ".claude"
    settings_dir.mkdir(parents=True, exist_ok=True)
    settings_path = settings_dir / "settings.local.json"

    new_settings = json.loads(format_claude_settings(mcps=mcps))

    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}

        # Merge permissions.allow (union, no duplicates)
        existing_perms = existing.get("permissions", {})
        existing_allow = set(existing_perms.get("allow", []))
        new_allow = set(new_settings["permissions"]["allow"])
        merged_allow = sorted(existing_allow | new_allow)

        existing.setdefault("permissions", {})["allow"] = merged_allow

        # Merge MCP server enablement
        existing_servers = set(existing.get("enabledMcpjsonServers", []))
        new_servers = set(new_settings.get("enabledMcpjsonServers", []))
        existing["enabledMcpjsonServers"] = sorted(existing_servers | new_servers)
        existing["enableAllProjectMcpServers"] = True

        merged = existing
    else:
        merged = new_settings

    settings_path.write_text(json.dumps(merged, indent=2) + "\n")
    return settings_path


def _action_color(action: str) -> str:
    if "started" in action:
        return "blue"
    if "completed" in action or "complete" in action:
        return "green"
    if "failed" in action:
        return "red"
    if "assigned" in action:
        return "yellow"
    return "white"


def _format_elapsed(seconds: float) -> str:
    """Format elapsed seconds into a human-readable string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        m, s = divmod(int(seconds), 60)
        return f"{m}m{s:02d}s"
    else:
        h, remainder = divmod(int(seconds), 3600)
        m, s = divmod(remainder, 60)
        return f"{h}h{m:02d}m"


def _ansi_color(rich_color: str) -> int:
    """Map a Rich color name to an ANSI color code."""
    mapping = {
        "cyan": 36,
        "green": 32,
        "yellow": 33,
        "magenta": 35,
        "blue": 34,
        "red": 31,
    }
    return mapping.get(rich_color, 37)  # default white


def main():
    try:
        fire.Fire(OdinCLI)
    except SystemExit:
        raise
    except Exception as exc:
        # Surface TaskItAuthError with a clean, actionable message
        from odin.backends.taskit import TaskItAuthError

        cause = exc
        while cause is not None:
            if isinstance(cause, TaskItAuthError):
                console = Console(stderr=True)
                console.print(f"\n[bold red]Authentication error:[/bold red] {cause}")
                raise SystemExit(1)
            cause = cause.__cause__ if cause.__cause__ else cause.__context__
        raise
