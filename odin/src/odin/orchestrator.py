"""Core orchestration engine."""

import asyncio
import json
import os
import random
import re
import traceback
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from odin.config import load_config
from odin.cost_tracking import CostStore, CostTracker
from odin.dependencies import DepStatus, check_deps, get_failed_deps, get_unmet_deps
from odin.harnesses import get_harness
from odin.harnesses.base import extract_text_from_stream, stream_json_is_complete
from odin.interactive import InteractivePlanSession
from odin.logging import OdinLogger, setup_logger, TaskContextAdapter
from odin.models import CostTier, OdinConfig, TaskResult
from odin.specs import SpecArchive, SpecStore, generate_spec_id, spec_short_tag
from odin.taskit import TaskManager
from odin.taskit.models import Task, TaskStatus
from odin import tmux

QUOTA_PROVIDER_MAP = {
    "claude": "claude_code",
    "codex": "codex",
    "gemini": "gemini",
    "qwen": "qwen",
    "minimax": "minimax",
    "glm": "glm",
}

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

        # Cost tracking — load pricing table for cost estimation
        self.cost_store = CostStore(self.config.cost_storage)
        pricing = self._load_pricing_table()
        self.cost_tracker = CostTracker(self.cost_store, pricing=pricing)
        # Availability cache to avoid redundant is_available() calls during planning
        self._availability_cache: Dict[str, bool] = {}

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

    # ------------------------------------------------------------------
    # Spec persistence
    # ------------------------------------------------------------------

    def _save_spec(self, spec: SpecArchive) -> None:
        """Save a spec archive, routing through backend when available."""
        if self._spec_backend:
            self._spec_backend.save_spec(spec)
        self.spec_store.save(spec)

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
        spec_archive = SpecArchive(
            id=sid,
            title=title,
            source=spec_file or "inline",
            content=spec,
            metadata={"working_dir": wd},
        )
        self._save_spec(spec_archive)

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
            transcript_path = self._run_interactive_plan(prompt, wd, quick=quick)
            elapsed_ms = (_time.monotonic() - t0) * 1000
            # Read transcript for trace capture
            raw_output = ""
            if transcript_path and Path(transcript_path).exists():
                raw_output = Path(transcript_path).read_text(errors="replace")
            decompose_result = TaskResult(
                success=True,
                output=raw_output,
                duration_ms=elapsed_ms,
                agent=self.config.base_agent,
            )
        else:
            decompose_result = await self._decompose(
                prompt, wd, spec_id=sid, stream_callback=stream_callback
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
        tasks = await self._create_tasks_from_plan(sub_tasks, sid, quota, routing_config)

        # 8. Post planning trace to backend (if captured)
        if decompose_result is not None:
            self._record_planning_trace(sid, decompose_result, prompt)

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
            base_cfg = self.config.agents.get(self.config.base_agent)
            model = (base_cfg.premium_model or base_cfg.default_model or "") if base_cfg else ""
            backend.record_planning_result(
                spec_id=spec_id,
                raw_output=result.output or "",
                duration_ms=result.duration_ms or 0,
                agent=result.agent or self.config.base_agent,
                model=model,
                effective_input=effective_input[:5000],
                success=result.success,
            )
        except Exception:
            self._log.warning(
                "Failed to post planning trace for spec %s",
                spec_id, exc_info=True,
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

        quick_instruction = ""
        if quick:
            quick_instruction = """

QUICK MODE: Do NOT explore or read the codebase. Do NOT use tools to inspect files, directories, or project structure. Generate the task plan directly and solely from the specification provided below. Work only with the information given in the spec."""

        return f"""You are a task planner. Break the specification below into sub-tasks for AI agents.{quick_instruction}

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

Write your final plan as a JSON array to: `{plan_path}`"""

    # ------------------------------------------------------------------
    # _create_tasks_from_plan()
    # ------------------------------------------------------------------

    async def _create_tasks_from_plan(
        self,
        sub_tasks: List[Dict[str, Any]],
        spec_id: str,
        quota: Optional[Dict[str, Dict[str, float]]] = None,
        routing_config: Optional[Dict[str, Any]] = None,
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
            agent_name, selected_model, routing_reasoning = await self._route_task(
                st.get("required_capabilities", []),
                complexity,
                st.get("suggested_agent"),
                quota,
                routing_config=routing_config,
            )

            task_metadata = {
                **st.get("metadata", {}),
                "required_capabilities": st.get("required_capabilities", []),
                "suggested_agent": st.get("suggested_agent"),
                "complexity": complexity,
                "routing_reasoning": routing_reasoning,
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
    # _run_interactive_plan()
    # ------------------------------------------------------------------

    def _run_interactive_plan(
        self,
        prompt: str,
        working_dir: str,
        quick: bool = False,
    ) -> Optional[str]:
        """Launch interactive tmux session for planning.

        The agent receives the unified plan prompt (same as auto/quiet) and
        writes its plan JSON to the plan_path specified in the prompt.
        Blocks until the user exits the tmux session.

        Returns the path to the transcript log (for trace capture), or None.
        """
        base_name = self.config.base_agent
        base_cfg = self.config.agents.get(base_name)
        if not base_cfg:
            raise RuntimeError(f"Base agent '{base_name}' not found in config")

        harness = get_harness(base_name, base_cfg)
        # Planning is high-judgment — always use premium model
        model = base_cfg.premium_model or base_cfg.default_model
        context = {"working_dir": working_dir}
        if model:
            context["model"] = model

        session = InteractivePlanSession(
            harness=harness,
            system_prompt=prompt,
            context=context,
            log_dir=self.config.log_dir,
        )
        # run() is synchronous (blocks while user is in tmux)
        return session.run()

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
        # Resolve prefix
        full_id = self.task_mgr.resolve_task_id(task_id) or task_id
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

        sem = asyncio.Semaphore(1)
        return await self._execute_task(
            full_id, task.assigned_agent, desc, working_dir, sem, mock=mock
        )

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
            if not content:
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
                proof_items.append(content)
            elif ctype == "status_update" and email.endswith("@odin.agent"):
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
            capped = [p[:500] for p in proof_items]
            sections.append(
                ("## Prior Proof of Work", "\n\n".join(capped))
            )

        if latest_agent_output:
            sections.append(
                ("## Previous Execution Output", latest_agent_output[:1000])
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
        full_id = self.task_mgr.resolve_task_id(task_id) or task_id
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

        # Use the task's assigned agent, or fall back to base agent
        agent_name = task.assigned_agent or self.config.base_agent
        agent_cfg = self.config.agents.get(agent_name)
        if not agent_cfg:
            agent_name = self.config.base_agent
            agent_cfg = self.config.agents.get(agent_name)
        if not agent_cfg:
            self._clear_summarize_flag(full_id)
            raise RuntimeError(f"No agent config found for '{agent_name}'")

        harness = get_harness(agent_name, agent_cfg)

        # Resolve working dir
        working_dir = str(Path.cwd())
        if task.spec_id:
            spec = self.spec_store.load(task.spec_id)
            if spec and spec.metadata.get("working_dir"):
                working_dir = spec.metadata["working_dir"]

        context = {"working_dir": working_dir}

        # Pick model from task metadata
        model = None
        if task.metadata:
            model = task.metadata.get("selected_model")
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

        Uses DFS-based cycle detection. Raises RuntimeError if a cycle is found.
        """
        tasks_by_id = {}
        for tid in task_ids:
            task = self.task_mgr.get_task(tid)
            if task:
                tasks_by_id[tid] = task

        WHITE, GRAY, BLACK = 0, 1, 2
        color = {tid: WHITE for tid in tasks_by_id}

        def dfs(tid: str, path: List[str]) -> None:
            color[tid] = GRAY
            task = tasks_by_id.get(tid)
            if not task:
                return
            for dep in task.depends_on:
                if dep not in color:
                    continue
                if color[dep] == GRAY:
                    cycle = path[path.index(dep):] + [dep]
                    titles = [tasks_by_id[c].title for c in cycle if c in tasks_by_id]
                    raise RuntimeError(
                        f"Dependency cycle detected: {' → '.join(titles)}"
                    )
                if color[dep] == WHITE:
                    dfs(dep, path + [dep])
            color[tid] = BLACK

        for tid in tasks_by_id:
            if color[tid] == WHITE:
                dfs(tid, [tid])

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
    ) -> Optional[str]:
        """Generate per-CLI MCP config files for the agent.

        Merges server entries from all configured MCP packages (controlled
        by ``self.config.mcps``) into a single config file per CLI format.

        For Claude, returns the config file path (for --mcp-config flag).
        For all other CLIs, writes to working_dir and returns None (auto-discovery).
        Returns None if no MCP servers are configured.
        """
        mcps = self.config.mcps
        has_taskit = "taskit" in mcps and self.config.taskit
        has_mobile = "mobile" in mcps
        has_chrome_devtools = "chrome-devtools" in mcps
        cd_headless = bool(
            self.config.chrome_devtools and self.config.chrome_devtools.headless
        )

        if not has_taskit and not has_mobile and not has_chrome_devtools:
            return None

        from odin.mcps.taskit_mcp.config import (
            MCP_CONFIG_MAP, server_entry as taskit_server_entry,
        )

        # Build taskit env only when taskit is configured
        env = self._get_mcp_env(task_id, agent_name, model=model) if has_taskit else {}

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
                if cd_headless:
                    cd_args.append("--headless")
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
                mcp_servers.update(cd_fragment(agent_name, headless=cd_headless))
                permission.update(cd_opencode_permissions())
            content = json.dumps({"permission": permission, "mcp": mcp_servers}, indent=2)
            wd = Path(working_dir) if working_dir else Path.cwd()
            config_path = wd / "opencode.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(content)
            self._log.debug(
                "[task:%s] Generated %s MCP config at %s", task_id, agent_name, config_path,
            )
            return None

        # --- mcpServers-based agents (claude, gemini, qwen, kilocode) ---
        servers: Dict = {}
        if has_taskit:
            servers.update(taskit_server_entry(agent_name, env))
        if has_mobile:
            from odin.mcps.mobile_mcp.config import server_fragment as mobile_fragment
            servers.update(mobile_fragment(agent_name))
        if has_chrome_devtools:
            from odin.mcps.chrome_devtools_mcp.config import server_fragment as cd_fragment
            servers.update(cd_fragment(agent_name, headless=cd_headless))

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

    async def _build_available_agents(
        self,
        quota: Optional[Dict[str, Dict[str, float]]] = None,
        routing_config: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Build the available agents list for planning prompts.

        When routing_config is available (from API), uses it as the primary
        source for agent/model data. Falls back to config-based agents.
        """
        available = []

        if routing_config and "agents" in routing_config:
            # API-sourced: richer model info with enabled/disabled state
            for agent_data in routing_config["agents"]:
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
                    "capabilities": agent_data.get("capabilities", []),
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
                        "capabilities": cfg.capabilities,
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
    ) -> TaskResult:
        """Dispatch the plan prompt to the base agent harness.

        The agent writes its plan JSON to the plan_path specified in the
        prompt.  This method handles only the harness dispatch — prompt
        building and plan reading happen in ``plan()``.

        In "auto" mode, ``stream_callback`` is called per chunk for
        terminal display.  In "quiet" mode, no callback is provided.

        Returns the TaskResult from the harness for trace capture.
        """
        base_name = self.config.base_agent
        base_cfg = self.config.agents.get(base_name)
        if not base_cfg:
            raise RuntimeError(f"Base agent '{base_name}' not found in config")

        harness = get_harness(base_name, base_cfg)

        self.logger.log(
            action="decompose_started", agent=base_name, input_prompt=prompt[:500]
        )

        # Planning is high-judgment — always use premium model
        model = base_cfg.premium_model or base_cfg.default_model
        log_dir = Path(self.config.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        output_file = str(log_dir / f"plan_{spec_id}.out")
        trace_file = str(log_dir / f"plan_{spec_id}.trace.jsonl")
        context = {
            "working_dir": working_dir,
            "output_file": output_file,
            "trace_file": trace_file,
        }
        if model:
            context["model"] = model
        start_ms = time.monotonic() * 1000

        if stream_callback:
            # Stream output chunks to terminal while agent writes plan to disk.
            # Tee raw JSONL to trace file so the planning log explorer works.
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
            if not result.success:
                raise RuntimeError(
                    f"Decomposition failed: {result.error or 'unknown error'}"
                )

        self.logger.log(
            action="decompose_completed",
            agent=base_name,
        )
        return result

    def _parse_json_array(self, text: str) -> List[Dict[str, Any]]:
        """Extract a JSON array from agent output (may contain markdown fences)."""
        # Try direct parse first
        text = text.strip()
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass

        # Try extracting from markdown code block
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1))
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                pass

        # Try finding array brackets
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                pass

        raise RuntimeError(f"Could not parse sub-tasks JSON from output: {text[:300]}")

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
    ) -> Tuple[str, Optional[str], str]:
        """Unified agent+model selection respecting LLM suggestions.

        Returns (agent, model, routing_reasoning).

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
                required_caps, complexity, suggested, quota, routing_config
            )
        return await self._route_task_config(
            required_caps, complexity, suggested, quota
        )

    async def _route_task_api(
        self,
        required_caps: List[str],
        complexity: str,
        suggested: Optional[str],
        quota: Optional[Dict[str, Dict[str, float]]],
        routing_config: Dict[str, Any],
    ) -> Tuple[str, Optional[str], str]:
        """Route using API-sourced agent/model data."""
        agents_data = routing_config["agents"]

        # Build index: agent_name -> agent_data
        agents_by_name = {a["name"]: a for a in agents_data}

        # Phase 1: honour the suggested agent if possible
        if suggested and suggested in agents_by_name:
            agent_data = agents_by_name[suggested]
            cfg = self.config.agents.get(suggested)
            if cfg and await self._is_available_cached(suggested, cfg):
                caps = agent_data.get("capabilities", [])
                if not required_caps or all(c in caps for c in required_caps):
                    if not self._over_quota(suggested, quota, complexity):
                        # Pick the default model for this agent
                        enabled_models = [
                            m["name"] for m in agent_data.get("models", [])
                            if m.get("enabled", True)
                        ]
                        model = agent_data.get("default_model")
                        if model and model not in enabled_models and enabled_models:
                            model = enabled_models[0]
                        elif not model and enabled_models:
                            model = enabled_models[0]
                        model, reason_suffix = self._maybe_upgrade_model_api(
                            suggested, model, complexity, agent_data
                        )
                        reasoning = f"Routed to {suggested}/{model} (suggested by planner{reason_suffix})"
                        return (suggested, model, reasoning)

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
            caps = agent_data.get("capabilities", [])
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

        # Group by tier, pick cheapest
        cheapest_tier = min(viable_routes, key=lambda r: tier_order.get(r[2], 1))[2]
        tier_candidates = [r for r in viable_routes if r[2] == cheapest_tier]

        chosen = random.choice(tier_candidates)
        agent, model, tier = chosen

        # Premium upgrade for high-complexity tasks
        agent_data = agents_by_name.get(agent, {})
        model, reason_suffix = self._maybe_upgrade_model_api(agent, model, complexity, agent_data)

        tier_names = sorted({r[0] for r in tier_candidates})
        reasoning = (
            f"Routed to {agent}/{model} ({tier.upper()} tier, "
            f"chosen from {len(tier_candidates)} viable {tier.upper()}-tier "
            f"route{'s' if len(tier_candidates) != 1 else ''}: "
            f"{', '.join(tier_names)}{reason_suffix})"
        )
        return (agent, model, reasoning)

    async def _route_task_config(
        self,
        required_caps: List[str],
        complexity: str,
        suggested: Optional[str],
        quota: Optional[Dict[str, Dict[str, float]]],
    ) -> Tuple[str, Optional[str], str]:
        """Route using config-based model_routing (fallback when API unavailable)."""
        # Phase 1: honour the suggested agent if possible
        if suggested:
            result = await self._try_routes_for_agent(
                suggested, required_caps, complexity, quota
            )
            if result:
                agent, model = result
                model, reason_suffix = self._maybe_upgrade_model(agent, model, complexity)
                reasoning = (
                    f"Routed to {agent}/{model} (suggested by planner{reason_suffix})"
                )
                return (agent, model, reasoning)

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
        cheapest_tier = min(viable_routes, key=lambda r: tier_order.get(r[2], 1))[2]
        tier_candidates = [r for r in viable_routes if r[2] == cheapest_tier]

        chosen = random.choice(tier_candidates)
        agent, model, tier = chosen
        model, reason_suffix = self._maybe_upgrade_model(agent, model, complexity)

        tier_names = sorted({r[0] for r in tier_candidates})
        reasoning = (
            f"Routed to {agent}/{model} ({tier.upper()} tier, "
            f"chosen from {len(tier_candidates)} viable {tier.upper()}-tier "
            f"route{'s' if len(tier_candidates) != 1 else ''}: "
            f"{', '.join(tier_names)}{reason_suffix})"
        )
        return (agent, model, reasoning)

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
        if required_caps and not all(
            cap in cfg.capabilities for cap in required_caps
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
    def _wrap_prompt(
        prompt: str,
        working_dir: Optional[str] = None,
        mcp_task_id: Optional[str] = None,
        mcps: Optional[List[str]] = None,
    ) -> str:
        """Append working directory, MCP guidance, and structured status envelope to a task prompt.

        When *mcp_task_id* is provided the prompt includes a section explaining
        the available TaskIt MCP tools and how to use them.  The ODIN-STATUS
        envelope stays as the programmatic fallback — MCP comments are for
        human visibility on the task board.
        """
        preamble = ""
        if working_dir:
            preamble = f"Working directory: {working_dir}\n\n"

        mcp_section = ""
        if mcp_task_id:
            mcp_section = f"""

## TaskIt MCP Tools

You have access to TaskIt MCP tools for communicating with the task board.
Your task ID is: {mcp_task_id}

You MUST follow this exact sequence — no steps may be skipped:

1. **Start**: call `taskit_add_comment` with comment_type="status_update" — what you're about to do
2. **Do your work** (write code, create files, etc.)
3. **Build & verify**: run the project's build command (e.g. `npm run build`, `python -m py_compile`, `cargo build`) and confirm it succeeds with zero errors. If the build fails, fix the errors before proceeding. A task is NOT done until the build passes.
4. **Proof**: call `taskit_add_comment` with comment_type="proof" and include:
   - `file_paths`: list every file you created or modified
   - A text summary describing what you did and how to verify it
   - The build command you ran and its result (pass/fail)
   THIS IS THE COMPLETION SIGNAL — a task without proof is incomplete and will be marked failed.
5. Then output the ODIN-STATUS block below.

If you are blocked and need human input, call `taskit_add_comment` with comment_type="question" — this pauses until a human replies.

DO NOT post a separate "completed" status_update. The proof comment IS your completion message.
DO NOT skip step 3. You must call taskit_add_comment with comment_type="proof" before outputting ODIN-STATUS.

"""

        chrome_devtools_section = ""
        if mcps and "chrome-devtools" in mcps:
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
        if mcps and "mobile" in mcps:
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

        return f"""{preamble}{prompt}{mcp_section}{chrome_devtools_section}{mobile_section}
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

        if "timeout" in text:
            failure_type = "timeout"
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

        Agent CLIs (Claude Code, Qwen, etc.) stream structured JSON where the
        agent's text response is embedded inside JSON string values.  The
        ODIN-STATUS envelope lives inside those values, so plain-text search
        on the raw output crosses JSON boundaries and produces broken content.

        This method detects the output format and extracts just the agent's
        text content:

        - **Claude Code JSONL**: ``{"type":"text","part":{"text":"..."}}``
        - **Gemini/Qwen stream-json**: ``{"type":"text","text":"..."}``
        - **Claude stream-json deltas**: ``{"type":"content_block_delta","delta":{"text":"..."}}``
        - **Claude/Gemini result**: ``{"type":"result","result":"..."}``
        - **Qwen CLI JSON**: ``{"subtype":"success","result":"..."}``
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
                # Gemini/Qwen stream-json: {"type":"text","text":"..."}
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

            # Qwen CLI: {"type":"result","subtype":"success","result":"..."}
            if obj.get("subtype") == "success" and "result" in obj:
                result_text = obj.get("result", "")
                if result_text:
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
    ) -> TaskResult:
        """Run a CLI command inside a tmux session and return a TaskResult."""
        start = time.monotonic()

        # Store tmux session name in task metadata
        task_obj = self.task_mgr.get_task(task_id)
        if task_obj:
            task_obj.metadata["tmux_session"] = tmux.session_name(task_id)
            self.task_mgr.update_task(task_obj)

        await tmux.launch(cmd, working_dir, task_id, output_file)
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

        if exit_code == 0:
            return TaskResult(
                success=True,
                output=stdout_text,
                duration_ms=round(duration, 1),
                agent=agent_name,
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
            )

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
            # Transition to EXECUTING unless already there (Celery path) or mock
            if not mock:
                task_obj = self.task_mgr.get_task(task_id)
                if not task_obj or task_obj.status != TaskStatus.EXECUTING:
                    self.task_mgr.update_status(task_id, TaskStatus.EXECUTING)

                # Store started_at timestamp in task metadata
                task_obj = self.task_mgr.get_task(task_id)
                if task_obj:
                    task_obj.metadata["started_at"] = time.time()
                    self.task_mgr.update_task(task_obj)

            # Read task metadata for model selection (works in both mock and normal)
            task_obj = self.task_mgr.get_task(task_id) if not mock else None
            model = None
            if task_obj and task_obj.metadata:
                model = task_obj.metadata.get("selected_model")

            self._log.info(
                "[task:%s] Execution started: agent=%s, model=%s, mock=%s",
                task_id, agent_name,
                model or "-", mock,
            )
            self.logger.log(
                action="task_started", task_id=task_id, agent=agent_name
            )

            cfg = self.config.agents[agent_name]
            harness = get_harness(agent_name, cfg)

            # Compute output file path for live tailing
            log_dir = Path(self.config.log_dir)
            output_file = str(log_dir / f"task_{task_id}.out")
            trace_file = str(log_dir / f"task_{task_id}.trace.jsonl")

            context = {
                "working_dir": working_dir,
                "output_file": output_file,
                "trace_file": trace_file,
                "timeout_seconds": self.config.execution_timeout_seconds,
            }
            if model:
                context["model"] = model

            # Generate MCP config for agent CLI integration
            # Claude returns a path (for --mcp-config flag); others write to working_dir
            # _generate_mcp_config returns None both when TaskIt is unconfigured AND
            # when the agent uses auto-discovery (non-Claude). Track whether MCP was
            # actually set up so the prompt can reference the tools.
            mcps = self.config.mcps
            has_taskit = "taskit" in mcps and bool(self.config.taskit)
            has_mobile = "mobile" in mcps
            has_chrome_devtools = "chrome-devtools" in mcps
            mcp_available = has_taskit or has_mobile or has_chrome_devtools
            mcp_config = self._generate_mcp_config(
                task_id, agent_name, log_dir, working_dir=working_dir, model=model,
            )
            if mcp_config:
                context["mcp_config"] = mcp_config

            # Pass MCP env dict so harnesses can inject CLI flags (e.g. Codex -c)
            if has_taskit:
                context["mcp_env"] = self._get_mcp_env(task_id, agent_name, model=model)
            if has_mobile:
                context["mobile_mcp_enabled"] = True
            if has_chrome_devtools:
                context["chrome_devtools_mcp_enabled"] = True
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

            wrapped = self._wrap_prompt(
                prompt, working_dir,
                mcp_task_id=task_id if mcp_available else None,
                mcps=mcps if mcp_available else None,
            )

            # Log effective input as debug comment for DAG debugging
            if not mock:
                self.task_mgr.add_comment(
                    task_id=task_id,
                    author="odin",
                    content=f"Effective input (with upstream context):\n\n{wrapped[:8000]}",
                    attachments=["debug:effective_input"],
                )

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
                    if Path(trace_file).exists():
                        raw_jsonl_for_backend = Path(trace_file).read_text()
                    elif Path(output_file).exists():
                        raw_jsonl_for_backend = Path(output_file).read_text()

                    failure_meta = self._classify_failure(exc, phase="task_execution")
                    stack_excerpt = self._sanitize_trace_excerpt(
                        traceback.format_exc(),
                        limit=1200,
                    )
                    error_message = f"Execution crashed: {type(exc).__name__}: {exc}"
                    execution_payload = {
                        "success": False,
                        "raw_output": raw_jsonl_for_backend,
                        "effective_input": wrapped[:PAYLOAD_EFFECTIVE_INPUT_LIMIT],
                        "error": error_message[:PAYLOAD_ERROR_MESSAGE_LIMIT],
                        "duration_ms": None,
                        "agent": harness.name or agent_name,
                        "metadata": {
                            "selected_model": model,
                            **({"taskit_run_token": task_run_token} if task_run_token else {}),
                            "failure_debug": stack_excerpt,
                        },
                        **failure_meta,
                    }
                    self.task_mgr.record_execution_result(
                        task_id=task_id,
                        execution_result=execution_payload,
                        status=TaskStatus.FAILED,
                        actor_email=self.task_mgr._format_actor_email(agent_name, model),
                    )
                raise

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

                # Store trace file path in task metadata
                if Path(trace_file).exists():
                    task_obj = self.task_mgr.get_task(task_id)
                    if task_obj:
                        task_obj.metadata["trace_file"] = trace_file
                        self.task_mgr.update_task(task_obj)

            result = raw_result
            agent_text = self._extract_agent_text(result.output or "")
            clean_output, parsed_success, summary = self._parse_envelope(agent_text)
            if parsed_success is not None:
                result = TaskResult(
                    success=parsed_success,
                    output=clean_output,
                    error=result.error,
                    duration_ms=result.duration_ms,
                    agent=result.agent,
                    metadata=result.metadata,
                )

            # Send raw result transparently — taskit owns all processing
            if not mock:
                # Read raw JSONL from trace file for backend processing
                raw_jsonl_for_backend = ""
                if Path(trace_file).exists():
                    raw_jsonl_for_backend = Path(trace_file).read_text()
                elif Path(output_file).exists():
                    raw_jsonl_for_backend = Path(output_file).read_text()
                else:
                    raw_jsonl_for_backend = result.output or ""

                new_status = TaskStatus.REVIEW if result.success else TaskStatus.FAILED
                payload_metadata = dict(result.metadata) if result.metadata else {}
                if estimated_cost_usd is not None:
                    payload_metadata["estimated_cost_usd"] = estimated_cost_usd
                if task_run_token:
                    payload_metadata["taskit_run_token"] = task_run_token
                self.task_mgr.record_execution_result(
                    task_id=task_id,
                    execution_result={
                        "success": result.success,
                        "raw_output": raw_jsonl_for_backend,
                        "effective_input": wrapped[:PAYLOAD_EFFECTIVE_INPUT_LIMIT],
                        "error": result.error,
                        "duration_ms": result.duration_ms,
                        "agent": result.agent or agent_name,
                        "metadata": payload_metadata,
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
                if Path(trace_file).exists():
                    raw_jsonl = Path(trace_file).read_text()
                elif Path(output_file).exists():
                    raw_jsonl = Path(output_file).read_text()
                else:
                    raw_jsonl = result.output or ""
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
