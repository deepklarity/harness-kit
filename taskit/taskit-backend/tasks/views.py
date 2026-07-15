import asyncio
import json
import os
import subprocess
import uuid
from datetime import datetime, time
from pathlib import Path
from collections import deque, namedtuple

import yaml

from django.conf import settings
from django.db import transaction
from django.db.models import Case, Count, F, IntegerField, Prefetch, Q, Value, When
from django.db import OperationalError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from .kanban_ordering import KANBAN_COLUMNS, get_statuses_for_column, move_task, order_column_queryset
from .failure_tagger import tag_failure_class
from .mistakes import record_execution_mistake, record_reflection_mistake
from .agent_stats import is_human_author
from .models import ErrorEvent  # noqa: E402 — error ledger (task #222)
from .board_story import build_board_story
from .factory_snapshot import build_factory_snapshot
from .inbox import build_inbox
from .rework import ReworkValidationError, compose_rework_task
from .spec_story import build_spec_story
from .models import (
    Board, BoardMembership, CommentAttachment, CommentType, Label,
    ReflectionReport, ReflectionStatus, ScheduleKind, ScheduleStatus, Spec, SpecComment, SpecCommentAttachment, Task,
    TaskComment, TaskHistory, TaskRunState, TaskSchedule, TaskScheduleRun, TaskStatus, User, UserRole, UserSetting,
    SystemSetting,
)
from . import task_runs
from .db import is_locked_error, retry_on_locked
from .audit_presets import load_presets
from .scheduling import (
    ACTIVE_OVERLAP_STATUSES,
    compute_schedule_next_run,
    create_schedule,
    EXECUTION_SCOPED_METADATA_KEYS,
    maybe_finalize_schedule_run,
    parse_local_datetime,
    local_to_utc,
    release_schedule_occurrence,
    rebind_local_datetime,
    ScheduleValidationError,
    TERMINAL_SUCCESS_STATUSES,
)
from .state_transitions import is_cancel_transition_allowed
from .ide import detect_supported_ides, get_supported_ide
from .utils.logger import logger
from .permissions import IsAdmin
from .forced_provider import (
    get_forced_provider_selection,
    is_model_allowed_for_forced_provider,
)
from .serializers import (
    AddLabelsSerializer,
    AssignTaskSerializer,
    BoardDetailSerializer,
    BoardListSerializer,
    BoardMemberIdsSerializer,
    BoardSerializer,
    CreateBoardSerializer,
    CreateScheduleSerializer,
    CommentAttachmentSerializer,
    CreatePlanningSpecSerializer,
    CreateSpecSerializer,
    CreateTaskCommentSerializer,
    ModelToggleSerializer,
    PlanningResultSerializer,
    RoutingAgentSerializer,
    ReworkTaskSerializer,
    SpecCommentSerializer,
    SpecCommentAttachmentSerializer,
    CreateTaskSerializer,
    ExecutionResultSerializer,
    LabelSerializer,
    MemberListSerializer,
    ReflectionReportSerializer,
    ReflectionReportUpdateSerializer,
    ReflectionRequestSerializer,
    OpenProjectSerializer,
    RuntimeStopSerializer,
    ScheduleStatusMutationSerializer,
    SetClaudeTokenSerializer,
    SpecDiagnosticSerializer,
    SpecListSerializer,
    SpecSerializer,
    StopExecutionSerializer,
    TaskCommentSerializer,
    TaskDashboardSerializer,
    TaskDetailSerializer,
    TaskHistorySerializer,
    TaskScheduleSerializer,
    TaskKanbanCardSerializer,
    TaskListSerializer,
    TaskSearchResultSerializer,
    TaskSerializer,
    TaskWithHistorySerializer,
    UnassignTaskSerializer,
    UpdateScheduleSerializer,
    UpdateTaskSerializer,
    UserSerializer,
    UserIdeSettingUpdateSerializer,
    UserSettingSerializer,
)

FAILURE_DEBUG_PREVIEW_LIMIT = 400
FAILURE_REASON_LIMIT = 500
FAILURE_ORIGIN_LIMIT = 200
EFFECTIVE_INPUT_LIMIT = 5000
EXECUTING_LOCKED_MUTATION_FIELDS = {
    "status": "status",
    "assignee_id": "assignee",
    "model_name": "model",
}
WEEKDAY_KEYS = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']



REFLECTION_PREFERRED_AGENTS = ["gemini", "codex", "claude"]

# Max automatic retries for REVIEWER_INFRA (truncated review output).
# Does NOT count against the 3-strike NEEDS_WORK/FAIL limit — infra
# retries are free because the reviewer never actually judged the work.
# After this many infra retries, the task parks in REVIEW for manual triage.
REVIEWER_INFRA_RETRY_CAP = 2

def _reflection_agent_hint_for_model(model_name):
    model = (model_name or "").lower()
    if model.startswith("gemini"):
        return "gemini"
    if model.startswith("claude"):
        return "claude"
    if model.startswith("gpt") or model.startswith("o") or "codex" in model:
        return "codex"
    if model.startswith("zai-coding-plan/"):
        return "glm"
    if model.startswith("minimax-coding-plan/"):
        return "minimax"
    return None



def _model_name(entry):
    """Normalize an ``available_models`` list entry to a model-name string.

    Entries may be either ``{"name": "x", ...}`` dicts (the canonical shape
    produced by ``data/agent_models.json`` + ``seedmodels``) or bare strings
    (the legacy shape some operational data still carries). Iterating with
    ``entry.get("name")`` crashes on a bare string — the surrounding ``any()``
    silently swallows the AttributeError and the resolver returns
    ``(None, None)`` even when the model is right there (task #246).
    """
    if isinstance(entry, dict):
        return entry.get("name")
    return entry


def _agent_available_model_names(agent_name):
    """Return sorted list of model names that ``agent_name`` advertises.

    Returns ``None`` (not ``[]``) when no agent user matches — callers
    distinguish "unknown agent" from "known agent with no models" so the
    400 response can surface the right error. Empty list means the agent
    exists but carries no models; ``None`` means the agent isn't a known
    reflection reviewer at all (the latter is already rejected by
    ``ReflectionRequestSerializer.validate_reviewer_agent`` upstream; this
    helper still tolerates the call so it stays safe in any context).
    """
    if not agent_name:
        return None
    lookup = (agent_name or "").strip().lower()
    matches = User.objects.filter(
        role=UserRole.AGENT,
        email__iendswith="@odin.agent",
        is_active=True,
    )
    for u in matches:
        local = (u.email or "").split("@")[0]
        name = local.split("+")[0].lower()
        if name == lookup:
            names = {
                _model_name(m)
                for m in (u.available_models or [])
                if _model_name(m)
            }
            return sorted(names)
    return None


# ── W3.18: size-bucketed reviewer selection ────────────────────────────────
# Default thresholds for the size buckets (in chars of estimated review
# context). The numbers are heuristics — small tasks (≤8KB) get a cheap
# reviewer, large tasks (≥24KB) get the default tier. The strategy on the
# board can override these per-board.
REFLECTION_SIZE_THRESHOLD_DEFAULT_SMALL_MAX = 8000
REFLECTION_SIZE_THRESHOLD_DEFAULT_LARGE_MIN = 24000


def _estimate_task_context_size(task):
    """Estimate the assembled reflection-prompt size for a task.

    The estimate is computed from the same fields Odin would include in
    the reviewer's prompt:
    - description
    - all comments (regardless of type — the reflection prompt truncates
      noisy types but never drops them)
    - full_output (agent's execution transcript stored in metadata)
    - dependencies (small constant ~80 chars per dep)
    - metadata_summary (~constant)

    The estimate doesn't need to be byte-exact with the assembled prompt;
    it just needs to bucket the task into the right range. Under-estimating
    is safer than over-estimating because it biases toward the stronger
    reviewer (no risk of undersized review on a borderline task).
    """
    if task is None:
        return 0
    description_len = len(task.description or "")
    full_output_len = 0
    metadata = task.metadata or {}
    full_output = metadata.get("full_output") or ""
    if isinstance(full_output, str):
        full_output_len = len(full_output)
    comments_len = 0
    try:
        # All comments counted — both status_update and proof/reply contribute.
        for c in task.comments.all():
            comments_len += len(c.content or "")
    except Exception:
        # Defensive: if the relation is unavailable, just skip the comments.
        comments_len = 0
    deps_len = 0
    try:
        deps = list(task.depends_on or [])
        deps_len = len(deps) * 80
    except Exception:
        deps_len = 0
    return description_len + comments_len + full_output_len + deps_len


def _bucket_for_context_size(context_size, thresholds=None):
    """Return the bucket name ("small" | "medium" | "large") for a size.

    Bucket boundaries are inclusive at the upper edge:
    - small: size < small_max
    - medium: small_max <= size < large_min
    - large: size >= large_min

    `thresholds` is a dict with optional "small_max" / "large_min"
    integers. Defaults apply per-key when missing.
    """
    th = thresholds or {}
    small_max = int(th.get("small_max", REFLECTION_SIZE_THRESHOLD_DEFAULT_SMALL_MAX))
    large_min = int(th.get("large_min", REFLECTION_SIZE_THRESHOLD_DEFAULT_LARGE_MIN))
    if context_size < small_max:
        return "small"
    if context_size < large_min:
        return "medium"
    return "large"


def _resolve_reviewer_for_model(model_name, board=None):
    """Find an agent that has `model_name` in its available_models.

    Mirrors the model-resolution behavior of the reviewer-order walk but
    keyed on a specific model name (used for reflection_model /
    reflection_review_strategy resolution).
    Returns (agent, model) or (None, None) when no agent carries it.
    """
    if not model_name:
        return None, None
    hinted = _reflection_agent_hint_for_model(model_name)
    agent_users = User.objects.filter(
        role=UserRole.AGENT,
        email__iendswith="@odin.agent",
        is_active=True,
    )
    by_agent = {}
    for u in agent_users:
        local = (u.email or "").split("@")[0]
        name = local.split("+")[0].lower()
        if u.available_models:
            by_agent[name] = u

    # Scope to board members if a board is provided and has agents.
    board_member_agents = None
    if board is not None:
        board_member_ids = set(
            BoardMembership.objects.filter(
                board=board, user__role=UserRole.AGENT,
            ).values_list("user_id", flat=True)
        )
        if board_member_ids:
            board_member_agents = {
                name for name, u in by_agent.items()
                if u.id in board_member_ids
            }

    candidates = board_member_agents or set(by_agent.keys())
    search_order = [hinted] if hinted else REFLECTION_PREFERRED_AGENTS
    for agent in search_order:
        if not agent or agent not in candidates:
            continue
        u = by_agent.get(agent)
        if not u:
            continue
        models = u.available_models or []
        if any(_model_name(m) == model_name for m in models):
            return agent, model_name
    return None, None


def _agent_settings_hint(board_id, agent_name=None):
    """Return the API URL the operator opens to flip the switch (W10.4).

    Mirrors the actual UI link the SettingsView renders so the dispatch
    error message and the action button point at the same place — the
    one-liner the operator sees in the traceback is the same one the
    dashboard shows.

    ``board_id`` is required; an ``agent_name`` is appended only when
    known so callers can land directly on the toggle row.
    """
    base = f"/api/boards/{board_id}/agents/"
    return base if not agent_name else f"{base}{agent_name}/"


def _board_enabled_agents(board):
    """Return {agent_name: User} for active AGENT users enrolled on `board`.

    Mirrors the scoping logic used by
    `_resolve_reviewer_for_model`: an
    "enabled" reviewer is a BoardMembership (role AGENT) whose User row is
    still active (retired agents keep their row for FK integrity but must
    not surface — task #135) and carries at least one available model.
    """
    member_ids = None
    if board is not None:
        member_ids = set(
            BoardMembership.objects.filter(
                board=board, user__role=UserRole.AGENT,
            ).values_list("user_id", flat=True)
        )

    agent_users = User.objects.filter(role=UserRole.AGENT, is_active=True)
    if member_ids:
        agent_users = agent_users.filter(id__in=member_ids)
    # No board, or a board with no enrolled agents yet: fall back to any
    # active AGENT user (legacy `_find_first_available_reviewer` behavior)
    # rather than returning nothing.

    by_agent = {}
    for u in agent_users:
        local = (u.email or "").split("@")[0]
        name = local.split("+")[0].lower()
        if name not in by_agent and u.available_models:
            by_agent[name] = u
    return by_agent


def _strongest_first_reviewer_order(by_agent):
    """Deterministic strongest-first (agent, model) list for a board.

    Mirrors the sort used for auto-populating `model_escalation_priority`:
    every (agent, model) pair the enabled agents carry, sorted by
    `output_price_per_1m_tokens` descending (most expensive/strongest
    first). Missing prices sort last. Ties break on (agent, model) name so
    the ordering — and therefore the fallback pick — never depends on
    dict/set iteration order.
    """
    from .pricing import get_pricing_table
    pricing = get_pricing_table()
    candidates = []
    for agent_name, user in by_agent.items():
        for entry in (user.available_models or []):
            model_name = _model_name(entry)
            if not model_name:
                continue
            price = pricing.get(model_name, {}).get("output_price_per_1m_tokens")
            candidates.append((agent_name, model_name, price))

    def _sort_key(item):
        _agent_name, _model_name_, price = item
        # Descending price → negate; None sorts after any real price.
        has_price = price is not None
        return (0 if has_price else 1, -price if has_price else 0, _agent_name, _model_name_)

    candidates.sort(key=_sort_key)
    return [{"agent_name": a, "model_name": m} for a, m, _p in candidates]


def _walk_reviewer_order(order, by_agent, quota_cache, exclude=frozenset()):
    """Walk an ordered [{agent_name, model_name}] list, first viable wins.

    A candidate is viable when: the agent is present in `by_agent` (enabled
    board member with available models), the model is one of that agent's
    available_models, and the agent's real-time quota isn't `exhausted`
    (`headroom` and `unavailable` are both usable — an unmapped or down
    quota checker must never brick reviewer selection).

    Returns (agent, model, rank) or (None, None, None). `quota_cache` is a
    dict the caller reuses across calls in the same selection so the walk
    never re-queries the same agent's usage twice.
    """
    for rank, entry in enumerate(order or []):
        if not isinstance(entry, dict):
            continue
        agent_name = (entry.get("agent_name") or "").strip().lower()
        model_name = entry.get("model_name")
        if not agent_name or not model_name:
            continue
        if (agent_name, model_name) in exclude:
            # Retry diversity: this reviewer's last verdict on the task
            # was unusable (ERROR/empty) — re-picking it deterministically
            # deadlocks retries (task 297: three identical ERRORs).
            continue
        user = by_agent.get(agent_name)
        if not user:
            continue
        models = user.available_models or []
        if not any(_model_name(m) == model_name for m in models):
            continue
        if agent_name not in quota_cache:
            quota_cache[agent_name] = _check_provider_usage(agent_name)
        if quota_cache[agent_name].state == _QUOTA_EXHAUSTED:
            continue
        return agent_name, model_name, rank
    return None, None, None


def select_reviewer_by_context_size(task, board=None, exclude_reviewers=frozenset()):
    """Select (agent, model, selection_reason) for a reflection run.

    ONE mechanism, operator directive: the board's ordered reviewer list.
    1. board.reviewer_order — ordered, board-configured walk. Skips agents
       that aren't enabled board members, models the agent doesn't carry,
       and agents whose real-time quota is exhausted.
       Returns "reviewer_order[<rank>]".
    2. Deterministic strongest-first fallback (by
       output_price_per_1m_tokens, descending) over the board's enabled
       agents, same quota filter. Returns "default_strongest".

    The legacy single-model override (board.reflection_model) and the
    size-bucket strategy (board.reflection_review_strategy) are retired:
    both were redundant once the ordered walk existed ("topmost available
    model with available quota is used"). Fields remain on the model for
    old rows but are never consulted. Forced-provider (env) likewise
    plays no role in reviewer selection.
    """
    by_agent = _board_enabled_agents(board)
    quota_cache = {}
    exclude = frozenset(exclude_reviewers or ())

    reviewer_order = getattr(board, "reviewer_order", None) if board else None
    agent, model, rank = _walk_reviewer_order(reviewer_order, by_agent, quota_cache, exclude)
    if agent and model:
        return agent, model, f"reviewer_order[{rank}]"

    fallback_order = _strongest_first_reviewer_order(by_agent)
    agent, model, _rank = _walk_reviewer_order(fallback_order, by_agent, quota_cache, exclude)
    if agent and model:
        return agent, model, "default_strongest"

    return None, None, "default_strongest"


def _normalize_directory_name(directory_name):
    name = (directory_name or "").strip()
    if not name:
        raise ValidationError({"directory_name": "Directory name cannot be empty."})
    if name in {".", ".."}:
        raise ValidationError({"directory_name": "Directory name cannot be '.' or '..'."})
    if "/" in name or "\\" in name:
        raise ValidationError({"directory_name": "Directory name must be a single path segment."})
    return name


def _normalize_managed_file_name(file_name):
    name = (file_name or "").strip()
    if not name:
        raise ValidationError({"file_name": "File name cannot be empty."})
    if name in {".", ".."}:
        raise ValidationError({"file_name": "File name cannot be '.' or '..'."})
    if "/" in name or "\\" in name:
        raise ValidationError({"file_name": "File name must stay in the board root."})
    if not name.lower().endswith(".md"):
        raise ValidationError({"file_name": "File name must end with .md"})
    return name



def _agent_provider_name(user):
    if not user or not user.email.endswith("@odin.agent"):
        return None
    return user.email.split("@")[0]


def _validate_forced_task_target(assignee=None, model_name=None):
    """Validate task assignment target.

    In forced-provider mode, the forced provider controls the planning brain
    and reflections, but tasks are routed to any available agent via normal
    cheapest-capable routing. No agent restriction is enforced here.
    """
    # No-op: routing is handled by odin's _route_task() which distributes
    # tasks across all available agents regardless of forced provider.
    return


def _validate_dispatch_readiness(board, spec_id, is_dispatch_transition):
    """Refuse a transition into IN_PROGRESS for a task with no spec on a
    board that hasn't opted into project-root execution.

    Without a spec there is no worktree for the executor to run in — see
    tasks/dag_executor.py poll_and_execute, which FAILS such a task with
    "dispatch halted — no worktree path resolvable..." once it's already
    on the board consuming a slot. That downstream guard stays in place as
    a last resort (schedules and other non-API paths can still reach
    IN_PROGRESS), but a normal PATCH/POST should be refused up front with
    an actionable message instead of round-tripping through a FAILED task.

    `is_dispatch_transition` must be True only when the request is actually
    moving the task into IN_PROGRESS — edits to a task that's already
    IN_PROGRESS (or any other field/status) must never be blocked here.
    """
    if not is_dispatch_transition:
        return
    if spec_id:
        return
    if board.allow_project_root_execution:
        return
    raise ValidationError({
        "status": (
            "This task has no spec, so there is no worktree to execute in. "
            "Plan it into a spec, or enable project-root execution in "
            "board settings."
        ),
    })


def _clear_stop_guards(metadata):
    metadata.pop("ignore_execution_results", None)
    metadata.pop("stopped_run_token", None)
    metadata.pop("execution_stopped_at", None)
    metadata.pop("pending_stop_target", None)
    metadata.pop("pending_stop_updated_by", None)
    metadata.pop("pending_stop_reason", None)


def _trigger_auto_reflection(task, exclude_reviewers=frozenset()):
    """Create a ReflectionReport and dispatch the Celery task if no active reflection exists.

    Called when a task transitions to REVIEW — mirrors the pattern used for
    auto-execution on IN_PROGRESS (explicit call in the view, not a signal).

    ``exclude_reviewers`` is a set of ``(agent_name, model_name)`` tuples to
    skip during reviewer selection. Used by the REVIEWER_INFRA retry path to
    prefer a fresh reviewer — the truncated reviewer may simply truncate
    again on the same task (task #346).
    """
    if task.skip_reflection or task.board.skip_reflection:
        source = "task" if task.skip_reflection else f"board {task.board_id}"
        logger.info("[task:%s] Skipping auto-reflection (%s) — dispatching merge+advance directly", task.id, source)
        _merge_task_on_reflection_pass(task)
        return

    active_exists = ReflectionReport.objects.filter(
        task=task,
        status__in=(ReflectionStatus.PENDING, ReflectionStatus.RUNNING),
    ).exists()
    if active_exists:
        logger.info(
            "[task:%s] Skipping auto-reflection: active reflection already exists",
            task.id,
        )
        return

    reviewer_agent, reviewer_model, selection_reason = select_reviewer_by_context_size(
        task, board=task.board, exclude_reviewers=exclude_reviewers,
    )
    if not reviewer_agent or not reviewer_model:
        if exclude_reviewers:
            # Fresh-reviewer preference eliminated all candidates. Fall
            # back to any available reviewer — retrying with the same one
            # is better than skipping the review entirely (task #346).
            reviewer_agent, reviewer_model, selection_reason = select_reviewer_by_context_size(
                task, board=task.board,
            )
    if not reviewer_agent or not reviewer_model:
        logger.info(
            "[task:%s] Skipping auto-reflection: no reviewer agent (%s) is available — dispatching merge+advance directly",
            task.id, "/".join(REFLECTION_PREFERRED_AGENTS),
        )
        _merge_task_on_reflection_pass(task)
        return

    report = ReflectionReport.objects.create(
        task=task,
        reviewer_agent=reviewer_agent,
        reviewer_model=reviewer_model,
        requested_by="system@taskit",
        context_selections=[
            "description", "comments", "execution_result",
            "dependencies", "metadata",
        ],
        status=ReflectionStatus.PENDING,
        selection_reason=selection_reason,
    )

    from .dag_executor import _dispatch_reflection_task
    _dispatch_reflection_task(report.id)

    logger.info(
        "[task:%s] Auto-reflection triggered: report_id=%s, reviewer=%s/%s, selection=%s",
        task.id, report.id, reviewer_agent, reviewer_model, selection_reason,
    )


def _merge_task_on_reflection_pass(task):
    """Dispatch merge of task branch into spec branch as a Celery task.

    Called when REVIEW → TESTING (pass) or REVIEW → FAILED (3 strikes).
    The actual merge runs in the Celery worker where odin is importable
    (the Django web process cannot import odin.worktree).

    When no branch exists (no worktree isolation), advances directly to
    TESTING since there is nothing to merge.

    W3.22 (task #171): every skip path now emits a log line AND posts a
    STATUS_UPDATE comment, and the dispatched path stamps
    ``merge_dispatched_at`` + ``merge_dispatch_attempts`` so the watchdog
    (see ``dag_executor.scan_pending_merge_dispatches``) can detect a
    stalled dispatch. The Celery ``.delay()`` call is wrapped in
    try/except so a broker outage cannot 500 the reflection PATCH or
    silently lose the merge — instead it surfaces as a STATUS_UPDATE
    comment + ``merge_status = "needs_human"``.
    """
    branch = (task.metadata or {}).get("branch")
    if not branch:
        # No branch — nothing to merge, advance directly.
        # Loud skip: the operator should see why TESTING happened without a merge.
        logger.info(
            "[task:%s] _merge_task_on_reflection_pass: no branch — "
            "advancing REVIEW → TESTING without merge",
            task.id,
        )
        from .dag_executor import _advance_task_to_testing
        _advance_task_to_testing(task)
        return

    # Skip if already merged (e.g. manual merge or duplicate call).
    # Loud skip: was a bare ``return`` in the original — that was the silent
    # path behind 9 wave-3 stalled tasks.
    if (task.metadata or {}).get("merge_status") == "merged":
        logger.warning(
            "[task:%s] _merge_task_on_reflection_pass: merge_status already "
            "'merged' — skipping duplicate dispatch",
            task.id,
        )
        _record_merge_skip(task, "already_merged",
                           "Merge dispatch skipped: branch already merged. "
                           "This is a duplicate dispatch — the merge is "
                           "already done.")
        return

    from .dag_executor import _dispatch_merge_task
    metadata = dict(task.metadata or {})
    metadata["merge_dispatch_attempts"] = int(metadata.get("merge_dispatch_attempts") or 0) + 1
    metadata["merge_dispatched_at"] = timezone.now().isoformat()
    metadata["merge_dispatch_source"] = "auto"
    Task.objects.filter(id=task.id).update(metadata=metadata)
    task.metadata = metadata
    logger.info(
        "[task:%s] _merge_task_on_reflection_pass: dispatching "
        "merge_task_on_reflection (attempt=%s)",
        task.id, metadata["merge_dispatch_attempts"],
    )
    try:
        _dispatch_merge_task(task.id)
    except Exception as exc:
        # Broker outage or queue full — surface it loudly so the watchdog
        # can pick it up on its next pass instead of leaving the task stalled.
        logger.exception(
            "[task:%s] _merge_task_on_reflection_pass: Celery dispatch failed",
            task.id,
        )
        meta = dict(task.metadata or {})
        meta["merge_status"] = "needs_human"
        meta["merge_dispatch_error"] = str(exc)
        Task.objects.filter(id=task.id).update(metadata=meta)
        task.metadata = meta
        # Error ledger (task #222): capture the celery broker failure
        # so the operator sees broker outages in the triage queue, not
        # only as buried dispatch-skip comments.
        try:
            from .errors import record_celery_exception
            record_celery_exception(
                task_name="tasks.dag_executor.merge_task_on_reflection",
                symptom=(
                    f"Merge dispatch failed for task {task.id}: "
                    f"{type(exc).__name__}: {exc}"
                ),
                exc_class=type(exc).__name__,
                exc_message=str(exc),
                task=task,
                task_id=task.id,
            )
        except Exception:
            logger.exception(
                "[task:%s] error ledger: failed to record celery_exception",
                task.id,
            )
        _record_merge_skip(
            task, "celery_dispatch_failed",
            f"Merge dispatch failed: Celery broker call raised {type(exc).__name__}: {exc}. "
            f"Watchdog will retry; if it stalls, run `odin merge {task.spec.odin_id if task.spec else '<spec>'}` manually.",
        )


def _record_merge_skip(task, reason, comment_text):
    """Loud-skip helper: log + post a STATUS_UPDATE comment for skipped merges.

    Every skip path in the post-PASS dispatch chain routes through this
    helper so the operator sees the stall on the task timeline instead of
    silently waiting on a watchdog.
    """
    metadata = dict(task.metadata or {})
    metadata["merge_dispatch_skip_reason"] = reason
    metadata["merge_dispatch_skip_at"] = timezone.now().isoformat()
    Task.objects.filter(id=task.id).update(metadata=metadata)
    task.metadata = metadata
    logger.warning("[task:%s] merge dispatch skipped: %s", task.id, reason)
    TaskComment.objects.create(
        task=task,
        schedule_run=task.current_schedule_run,
        author_email="system@taskit",
        author_label="system",
        content=comment_text,
        comment_type=CommentType.STATUS_UPDATE,
    )


# -- Quota keywords matched against failure_reason, verdict_summary, and quota_failure --
# Single source of truth lives in failure_tagger — both the tagger and
# _is_quota_failure must stay in lockstep.
from .failure_tagger import QUOTA_KEYWORDS as _QUOTA_KEYWORDS  # noqa: E402


def _is_quota_failure(task, report):
    """Detect whether a task failure was caused by quota/rate-limit exhaustion.

    Checks three sources (in order):
    1. Reflection report's quota_failure field (most reliable — reviewer detected it)
    2. Task metadata last_failure_type == "llm_call_failure" + quota keywords in reason
    3. Verdict summary containing quota-related keywords
    """
    # 1. Reflection explicitly flagged quota failure. The field is free text from
    # the reviewer, so require an AFFIRMATIVE quota signal — never infer "yes"
    # from arbitrary phrasing. (Reflection #90 wrote "None detected in current
    # execution output."; the old exact-match-on-"none." negation coerced that
    # into a quota failure and reassigned the task to a dead provider — F45.)
    quota_field = (getattr(report, "quota_failure", "") or "").strip().lower()
    if quota_field and any(kw in quota_field for kw in _QUOTA_KEYWORDS):
        return True

    # 2. Task metadata from orchestrator's failure classification
    meta = task.metadata or {}
    failure_type = (meta.get("last_failure_type") or "").lower()
    failure_reason = (meta.get("last_failure_reason") or "").lower()
    if failure_type == "llm_call_failure" and any(
        kw in failure_reason for kw in _QUOTA_KEYWORDS
    ):
        return True

    # 3. Verdict summary mentions quota/rate limit
    summary = (report.verdict_summary or "").lower()
    if any(kw in summary for kw in _QUOTA_KEYWORDS):
        return True

    return False


# -- Ground-truth quota verification (task #159) --------------------------------
# A keyword match can't distinguish a transient per-minute 429 (provider has
# headroom) from real quota exhaustion. Before reassigning away from a healthy
# provider, consult harness_usage_status for its actual usage. Only when the
# provider is genuinely exhausted (or the checker is unavailable and we fall
# back to the old keyword behavior) do we switch providers.

# Above this % of the active quota window a provider counts as exhausted and is
# safe to reassign away from. Task #154 was moved minimax->glm at 64% used; the
# ~95% bar keeps the switch for real exhaustion only.
_QUOTA_EXHAUSTED_PCT = 95.0

# Agent identity (from _agent_key) -> harness_usage_status provider name. Only
# claude differs; mirrors odin's QUOTA_PROVIDER_MAP (orchestrator.py).
_AGENT_TO_USAGE_PROVIDER = {
    "claude": "claude_code",
    "codex": "codex",
    "gemini": "gemini",
    "minimax": "minimax",
    "glm": "glm",
}

# Verification result states.
_QUOTA_EXHAUSTED = "exhausted"      # usage_pct >= threshold -> reassign
_QUOTA_HEADROOM = "headroom"        # usage_pct < threshold -> backoff, keep agent
_QUOTA_UNAVAILABLE = "unavailable"  # checker missing/errored -> keyword fallback

QuotaGroundTruth = namedtuple("QuotaGroundTruth", ["state", "usage_pct", "detail"])


def _run_usage_coro(coro):
    """Run an async get_usage() coroutine to completion from sync code.

    Mirrors analytics.py: handles the 'already inside a running event loop'
    case (ASGI) via nest_asyncio, then a fresh-thread fallback. asyncio.run
    raises RuntimeError before touching the coroutine when a loop is already
    running, so the coroutine can be safely reused in the fallback.
    """
    try:
        return asyncio.run(coro)
    except RuntimeError:
        try:
            import nest_asyncio
            nest_asyncio.apply()
            return asyncio.run(coro)
        except ImportError:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()


def _get_usage_from_provider(provider_name):
    """Fetch usage_pct for a provider via harness_usage_status.

    Returns ``(usage_pct, error)`` where exactly one element is meaningful:
      - ``(float, None)`` on success
      - ``(None, str)`` when the checker is unavailable or errored

    This is the network boundary; callers apply the threshold policy. Split
    out so tests can patch the seam without exercising the real provider HTTP.
    """
    try:
        from harness_usage_status.config import load_config
        from harness_usage_status.providers.registry import get_provider
    except ImportError:
        return None, "harness_usage_status package not installed"

    try:
        config = load_config()
        configs = config.get_provider_configs()
        if provider_name not in configs:
            return None, f"provider '{provider_name}' not configured"
        provider = get_provider(provider_name, configs[provider_name])
        usage = _run_usage_coro(provider.get_usage())
        usage.compute_pct()
    except Exception as e:
        return None, f"usage check raised: {e}"

    if usage.raw and usage.raw.get("error"):
        return None, f"usage check error: {usage.raw['error']}"
    if usage.usage_pct is None:
        return None, "usage_pct unavailable from provider"
    return usage.usage_pct, None


def _check_provider_usage(agent_key):
    """Verify the failing provider's real usage against ground truth.

    Returns a :class:`QuotaGroundTruth` (never raises). Maps the agent to its
    provider name, fetches real usage, and classifies it against the threshold.
    Every failure mode collapses to UNAVAILABLE so the caller can fall back to
    keyword-based reassignment with an honest "unverified" comment.
    """
    provider_name = _AGENT_TO_USAGE_PROVIDER.get((agent_key or "").lower())
    if not provider_name:
        return QuotaGroundTruth(
            _QUOTA_UNAVAILABLE, None,
            f"no usage provider mapped for agent '{agent_key}'",
        )

    pct, err = _get_usage_from_provider(provider_name)
    if err is not None:
        return QuotaGroundTruth(_QUOTA_UNAVAILABLE, None, err)

    if pct >= _QUOTA_EXHAUSTED_PCT:
        return QuotaGroundTruth(
            _QUOTA_EXHAUSTED, pct,
            f"{pct:.1f}% used (>= {_QUOTA_EXHAUSTED_PCT:g}% threshold)",
        )
    return QuotaGroundTruth(
        _QUOTA_HEADROOM, pct,
        f"{pct:.1f}% used (< {_QUOTA_EXHAUSTED_PCT:g}% threshold)",
    )


def _find_alternative_agent(task):
    """Find an alternative AGENT user on the same board, different from current assignee.

    Returns (User, model_name) or (None, None) if no alternative is available.
    Selects from board members with role=AGENT, excluding the current assignee.
    Falls back to any AGENT user if no board-scoped alternatives exist.

    Prefers a candidate in the same cost_tier as the current assignee (per
    agent_models.json), so a quota failure doesn't silently downgrade (or
    upgrade) the task to a differently-priced agent. Falls back to any
    candidate if no same-tier match exists.
    """
    from .models import BoardMembership, UserRole
    from .pricing import get_agent_cost_tiers, get_active_agents

    current_assignee_id = task.assignee_id
    current_assignee = task.assignee
    board_id = task.board_id

    # Only agents in the ACTIVE lineup (agent_models.json) are eligible.
    # DB agent rows outlive retirement (seedmodels never prunes), and picking
    # one dispatched #114 to dead gemini — F45.
    active = get_active_agents()

    def _active_only(users):
        return [u for u in users if _agent_key(u) in active]

    # Prefer agents that are members of the same board
    board_agent_ids = BoardMembership.objects.filter(
        board_id=board_id,
        user__role=UserRole.AGENT,
    ).exclude(
        user_id=current_assignee_id,
    ).values_list("user_id", flat=True)

    candidates = _active_only(User.objects.filter(
        id__in=board_agent_ids, role=UserRole.AGENT,
    ).order_by("id"))

    if not candidates:
        # Fallback: any ACTIVE agent user not the current one
        candidates = _active_only(User.objects.filter(role=UserRole.AGENT).exclude(
            id=current_assignee_id,
        ).order_by("id"))

    if not candidates:
        return None, None

    # Order candidates by the board's standing preference order (task #328)
    # so the routing peer is deterministic and operator-controlled — the
    # same order the web settings "Routing" section shows and edits. Agents
    # not named in the order sort last, keeping their stable id ordering.
    from .failure_policy import effective_preference_order
    pref = effective_preference_order(getattr(task, "board", None))
    pref_index = {name: i for i, name in enumerate(pref)}

    def _pref_key(user):
        return pref_index.get(_agent_key(user), len(pref))

    candidates = sorted(candidates, key=lambda c: (_pref_key(c), c.id))

    # Prefer a candidate in the same cost tier as the current assignee,
    # honouring the preference order within that tier.
    agent = candidates[0]
    if current_assignee:
        cost_tiers = get_agent_cost_tiers()
        current_tier = cost_tiers.get(_agent_key(current_assignee))
        if current_tier:
            same_tier = next(
                (c for c in candidates if cost_tiers.get(_agent_key(c)) == current_tier),
                None,
            )
            if same_tier:
                agent = same_tier

    # Model comes from the same single source (agent_models.json default,
    # then the user's own model data) — never a stale first-list-entry.
    model_name = _default_model_for_user(agent)
    if not model_name:
        available = agent.available_models or []
        if available and isinstance(available[0], str):
            model_name = available[0]

    return agent, model_name


def _forced_provider_response():
    selection = get_forced_provider_selection()
    return {
        "enabled": selection.enabled,
        "provider": selection.provider,
        "model": selection.model,
        "source": selection.source,
    }


def _fallback_task_user():
    admin = User.objects.filter(is_admin=True).order_by("id").first()
    if admin:
        return admin
    return User.objects.order_by("id").first()


def _request_task_user(request):
    user = getattr(request, "taskit_user", None) or getattr(request, "user", None)
    if isinstance(user, User):
        return user
    return _fallback_task_user()


def _user_settings_for_request(request):
    user = _request_task_user(request)
    if user is None:
        return None, None
    settings_obj, _ = UserSetting.objects.get_or_create(user=user)
    return user, settings_obj


def _board_project_root(task):
    root = (task.board.working_dir or "").strip()
    return root or None


def _maybe_reassign_on_quota_failure(task, report):
    """If the task failed due to quota exhaustion, reassign — but only after
    verifying against ground truth that the provider is actually exhausted.

    Task #159: keyword matching alone can't tell a transient per-minute 429
    (provider has headroom) from real quota exhaustion. Before reassigning,
    consult harness_usage_status for the failing provider's real usage:

      - EXHAUSTED (>= _QUOTA_EXHAUSTED_PCT): reassign (the real escape hatch).
      - HEADROOM (< threshold): a transient rate-limit. Keep the same agent,
        record metadata.rate_limit_backoff so repeats are visible, and let
        the caller requeue. Do NOT switch providers.
      - UNAVAILABLE (checker missing/errored): fall back to the pre-#159
        keyword-based reassignment, but flag it as "unverified" in the comment.

    Mutates task in place (assignee, model_name, metadata) and records history
    + comment. Returns True when this function posted the operator-facing
    explanation (so the caller suppresses its continuity comment); False when
    the failure was not a quota failure and this function was a no-op.
    """
    if not _is_quota_failure(task, report):
        return False

    task.refresh_from_db(fields=["assignee_id", "model_name", "metadata"])
    old_assignee = task.assignee
    old_model = task.model_name
    old_assignee_name = old_assignee.name if old_assignee else "unassigned"
    agent_key = _agent_key(old_assignee)

    verification = _check_provider_usage(agent_key)

    # Transient rate-limit with headroom: keep the agent, record a backoff
    # signal, do NOT reassign. The caller requeues the same agent.
    if verification.state == _QUOTA_HEADROOM:
        _record_rate_limit_backoff(task, agent_key, verification)
        TaskComment.objects.create(
            task=task,
            author_email="system@taskit",
            author_label="system",
            content=(
                f"Transient rate-limit detected for {old_assignee_name} ({old_model or 'unknown model'}) "
                f"but provider has headroom ({verification.detail}). Keeping assignee and requeuing "
                f"with backoff; recorded metadata.rate_limit_backoff."
            ),
            comment_type=CommentType.STATUS_UPDATE,
        )
        logger.info(
            "[task:%s] Quota keywords matched but %s has headroom (%s) — backoff, no reassignment.",
            task.id, agent_key or "unknown", verification.detail,
        )
        return True

    verified = verification.state == _QUOTA_EXHAUSTED
    unverified_note = (
        "" if verified
        else f" — unverified: usage check unavailable ({verification.detail})"
    )

    new_agent, new_model = _find_alternative_agent(task)
    if new_agent is None:
        logger.warning(
            "[task:%s] Quota failure detected (verified=%s) but no alternative agent available",
            task.id, verified,
        )
        TaskComment.objects.create(
            task=task,
            author_email="system@taskit",
            author_label="system",
            content=(
                f"Quota/rate-limit failure detected for {old_assignee_name} ({old_model or 'unknown model'})"
                f"{unverified_note}, but no alternative agent is available for reassignment."
            ),
            comment_type=CommentType.STATUS_UPDATE,
        )
        return True

    # Update task fields
    task.assignee = new_agent
    if new_model:
        task.model_name = new_model
    # Bump cumulative rework-round counter. The continuity path bumps it
    # for the non-quota case; this branch is mutually exclusive (continuity
    # short-circuits when assignee/model moved), so the bump lives here
    # for the quota-reassign case to keep one number per retry.
    metadata = dict(task.metadata or {})
    metadata["rework_count"] = int(metadata.get("rework_count", 0) or 0) + 1
    task.metadata = metadata
    task.save(update_fields=["assignee_id", "model_name", "metadata"])

    # Record history for assignee change
    TaskHistory.objects.create(
        task=task,
        field_name="assignee",
        old_value=old_assignee_name,
        new_value=new_agent.name,
        changed_by="system@taskit",
    )
    if new_model and new_model != old_model:
        TaskHistory.objects.create(
            task=task,
            field_name="model",
            old_value=old_model or "",
            new_value=new_model,
            changed_by="system@taskit",
        )

    # Post explanatory comment naming which path fired.
    verified_prefix = (
        f"VERIFIED exhausted ({verification.detail})" if verified else "detected"
    )
    TaskComment.objects.create(
        task=task,
        author_email="system@taskit",
        author_label="system",
        content=(
            f"Quota/rate-limit failure {verified_prefix} for {old_assignee_name} ({old_model or 'unknown model'}){unverified_note}. "
            f"Reassigned to {new_agent.name} ({new_model or 'default model'}) for retry."
        ),
        comment_type=CommentType.STATUS_UPDATE,
    )

    logger.info(
        "[task:%s] Quota failure reassignment (verified=%s): %s/%s → %s/%s",
        task.id, verified, old_assignee_name, old_model, new_agent.name, new_model,
    )
    return True


def _record_rate_limit_backoff(task, agent_key, verification):
    """Record a rate-limit backoff signal on task metadata (task #159).

    A transient 429 with provider headroom should not switch providers, but the
    signal must be visible so repeats are detectable and a future iteration can
    apply a real delay. Mirrors the metadata write pattern in
    ``_record_rework_continuity``.
    """
    metadata = dict(task.metadata or {})
    metadata["rate_limit_backoff"] = {
        "at": timezone.now().isoformat(),
        "agent": agent_key,
        "usage_pct": verification.usage_pct,
        "detail": verification.detail,
    }
    task.metadata = metadata
    task.save(update_fields=["metadata"])


def _record_rework_continuity(task, original_assignee_id, original_model, suppress_comment=False):
    """Record that a NEEDS_WORK retry preserved assignee+model (F45 mandate #1).

    The default for a NEEDS_WORK retry is to keep what the operator picked.
    The audit trail must reflect that decision even when the values match
    pre- and post-transition — otherwise the retry looks the same as a benign
    re-fire and the operator can't tell when continuity mattered.

    Side effects:
      - Stamps ``task.metadata["last_rework_reason"]="rework_continuity"`` and
        ``task.metadata["last_rework_at"]=<iso now>`` for at-a-glance reads.
      - Writes a TaskHistory row with ``field_name="assignee"`` so the
        duration-since-last-change readouts still re-anchor at the retry.
      - Writes a TaskHistory row with ``field_name="model"`` likewise.
      - Posts a STATUS_UPDATE comment naming the preserved agent+model —
        unless ``suppress_comment`` is True, in which case the quota path
        already surfaced the explanation and a second comment would be noise
        that overwrites the most-recent comment.

    The quota reassign path writes its own history rows inside
    ``_maybe_reassign_on_quota_failure``; if anything actually changed, this
    function short-circuits to avoid double-recording.
    """
    task.refresh_from_db(fields=["assignee_id", "model_name", "metadata"])

    if task.assignee_id != original_assignee_id or task.model_name != original_model:
        # Something changed (quota path). Skip — those rows are already
        # written by `_maybe_reassign_on_quota_failure`.
        return

    metadata = dict(task.metadata or {})
    metadata["last_rework_reason"] = "rework_continuity"
    metadata["last_rework_at"] = timezone.now().isoformat()
    # Cumulative rework-round counter so the operator can see at a glance
    # how many times this task has been re-dispatched. Distinct from
    # ``auto_redispatch_count`` (infra-class only) — this covers every
    # NEEDS_WORK retry regardless of failure class.
    metadata["rework_count"] = int(metadata.get("rework_count", 0) or 0) + 1
    task.metadata = metadata
    task.save(update_fields=["metadata"])

    assignee_label = (
        task.assignee.name if task.assignee else "unassigned"
    )

    TaskHistory.objects.create(
        task=task,
        field_name="assignee",
        old_value=str(original_assignee_id or ""),
        new_value=str(original_assignee_id or ""),
        changed_by="system@taskit",
    )
    TaskHistory.objects.create(
        task=task,
        field_name="model",
        old_value=original_model or "",
        new_value=original_model or "",
        changed_by="system@taskit",
    )
    if suppress_comment:
        # Quota path posted its own explanation (reassign comment OR
        # "no alternative agent available" warning). The history + metadata
        # stamp above is enough — adding a second comment would clobber the
        # operator-visible "most recent comment" with redundant noise.
        logger.info(
            "[task:%s] Rework continuity recorded (comment suppressed: quota "
            "path already explained)",
            task.id,
        )
        return
    TaskComment.objects.create(
        task=task,
        author_email="system@taskit",
        author_label="system",
        content=(
            f"Rework on task kept continuity: assignee '{assignee_label}' "
            f"and model '{task.model_name or 'unset'}' preserved "
            f"(F45 default-first mandate)."
        ),
        comment_type=CommentType.STATUS_UPDATE,
    )
    logger.info(
        "[task:%s] Rework continuity recorded: assignee=%s model=%s preserved",
        task.id, original_assignee_id, original_model,
    )


def _maybe_escalate_model(task):
    """Escalate a task exactly one deliberate tier — the CAPABILITY path.

    Task #328: a deliberate tier jump is the correct response to a
    *capability* failure — repeated review rejection, meaning the agent
    keeps producing work the reviewer won't accept. It is the WRONG
    response to a protocol/infra hiccup (that path retries same → peer,
    never a tier jump — see tasks.failure_policy). This function is the
    single sanctioned tier-escalation mechanism and is now reached ONLY
    from the reflection rework loop, after ``capability_escalate_after``
    review rejections — never from the execution-failure path.

    Uses the board's model_escalation_priority list. Index 0 = highest priority (rank 1).
    Walks upward from the current model's position toward index 0 (one step).

    Mutates task in place (assignee, model_name, metadata). Does NOT save.
    Returns True if escalation happened, False otherwise.
    """
    from .failure_policy import capability_policy

    task.metadata = task.metadata or {}

    def _skip(reason):
        task.metadata["escalation_skip_reason"] = reason
        return False

    # task #328: the enabled flag and retry cap come from the ONE policy
    # engine, not raw board fields — so routing_policy (the editable
    # settings surface) governs the tier jump. capability_policy resolves
    # routing_policy first, then legacy board fields (Default First).
    cap = capability_policy(task.board)
    if not cap.enabled:
        return _skip("disabled")

    priority_list = task.board.model_escalation_priority or []
    if not priority_list:
        return _skip("no_priority_list")

    # Check max escalations limit (policy-resolved)
    max_retries = cap.max_escalations
    current_escalation_count = task.metadata.get("escalation_count", 0)
    if current_escalation_count >= max_retries:
        return _skip("max_retries_reached")

    current_model = task.model_name or task.metadata.get("selected_model")
    if not current_model:
        return _skip("no_current_model")

    # Find current model's index in the priority list
    current_index = None
    for i, entry in enumerate(priority_list):
        if entry.get("model_name") == current_model:
            current_index = i
            break

    if current_index is None:
        return _skip("model_not_in_list")

    if current_index == 0:
        return _skip("already_highest")

    target = priority_list[current_index - 1]
    target_model = target["model_name"]
    target_agent_name = target["agent_name"]

    # Find the User object for the target agent
    agent_email = f"{target_agent_name}@odin.agent"
    try:
        target_user = User.objects.get(email=agent_email)
    except User.DoesNotExist:
        logger.warning(
            "[task:%s] Escalation target agent %s not found",
            task.id, agent_email,
        )
        return False

    old_assignee = task.assignee
    old_agent_name = old_assignee.name if old_assignee else "unassigned"
    old_model = task.model_name

    # Update task fields
    task.assignee = target_user
    task.model_name = target_model

    # Update metadata
    task.metadata = task.metadata or {}
    escalation_history = task.metadata.get("escalation_history", [])
    escalation_history.append({
        "from_model": old_model,
        "from_agent": old_agent_name,
        "to_model": target_model,
        "to_agent": target_agent_name,
    })
    task.metadata["escalation_history"] = escalation_history
    task.metadata["escalation_count"] = len(escalation_history)
    task.metadata["escalation_max"] = max_retries

    # Record history entries
    TaskHistory.objects.create(
        task=task,
        field_name="model",
        old_value=old_model or "",
        new_value=target_model,
        changed_by="system@taskit",
    )
    TaskHistory.objects.create(
        task=task,
        field_name="assignee",
        old_value=old_agent_name,
        new_value=target_user.name,
        changed_by="system@taskit",
    )

    # Post system comment naming the rule that fired (task #328: no silent
    # switches). The rejection count is stamped on metadata by the caller.
    rejections = task.metadata.get("capability_rejections")
    rejection_note = f" after {rejections} review rejections" if rejections else ""
    TaskComment.objects.create(
        task=task,
        author_email="system@taskit",
        author_label="system",
        content=(
            f"Capability failure: {old_agent_name}/{old_model or 'unknown'} "
            f"kept failing review{rejection_note} — escalating exactly one "
            f"tier to {target_agent_name}/{target_model} (deliberate tier jump)."
        ),
        comment_type=CommentType.STATUS_UPDATE,
    )

    logger.info(
        "[task:%s] Capability escalation (one tier): %s/%s → %s/%s",
        task.id, old_agent_name, old_model, target_agent_name, target_model,
    )
    return True


@api_view(["GET", "PATCH"])
def user_ide_settings(request):
    user, settings_obj = _user_settings_for_request(request)
    if user is None or settings_obj is None:
        return Response({"detail": "No TaskIt user is available for IDE settings."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(UserSettingSerializer(settings_obj).data)

    ser = UserIdeSettingUpdateSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    preferred_ide_id = (ser.validated_data.get("preferred_ide_id") or "").strip().lower() or None

    if preferred_ide_id:
        supported = get_supported_ide(preferred_ide_id)
        if supported is None:
            raise ValidationError({"preferred_ide_id": "Unsupported IDE."})
        detected_ids = {ide.id for ide in detect_supported_ides()}
        if preferred_ide_id not in detected_ids:
            raise ValidationError({"preferred_ide_id": "IDE is not currently detected on this system."})

    settings_obj.preferred_ide_id = preferred_ide_id
    _save_with_retry(settings_obj, update_fields=["preferred_ide_id", "updated_at"])
    return Response(UserSettingSerializer(settings_obj).data)


@api_view(["POST"])
def error_event_disposition(request, event_id):
    """Update the disposition of one ErrorEvent (task #222).

    Sets ``disposition`` to one of ``open`` / ``fixed`` / ``non-issue``
    and optionally records a triage note. Returns 404 when the event
    doesn't exist, 400 when the value is unknown.
    """
    from .errors import set_disposition as _set_disposition
    payload = request.data if isinstance(request.data, dict) else {}
    new_disposition = (payload.get("disposition") or "").strip()
    note = (payload.get("note") or "").strip()
    try:
        evt = _set_disposition(int(event_id), new_disposition, note=note)
    except ValueError as exc:
        return Response(
            {"detail": str(exc), "allowed": [c for c, _ in ErrorEvent.DISPOSITION_CHOICES]},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except ErrorEvent.DoesNotExist:
        return Response(
            {"detail": f"ErrorEvent {event_id} not found."},
            status=status.HTTP_404_NOT_FOUND,
        )
    from .errors import serialize_error_event
    return Response(serialize_error_event(evt))


@api_view(["GET"])
def user_ide_options(request):
    _, settings_obj = _user_settings_for_request(request)
    preferred_ide_id = settings_obj.preferred_ide_id if settings_obj else None
    detected = detect_supported_ides()
    return Response({
        "preferred_ide_id": preferred_ide_id,
        "detected_ides": [
            {"id": ide.id, "label": ide.label, "icon_key": ide.icon_key}
            for ide in detected
        ],
    })


def _executing_lock_response(task, attempted_fields):
    joined = ", ".join(attempted_fields)
    return Response(
        {
            "detail": (
                f"Task {task.id} is EXECUTING. Stop execution before changing: {joined}."
            ),
            "code": "task_executing_locked",
            "locked_fields": attempted_fields,
        },
        status=status.HTTP_409_CONFLICT,
    )


def _normalize_model_name(value):
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value or None


def _is_locked_field_change(task, field_key, incoming_value):
    if field_key == "status":
        return str(task.status) != str(incoming_value)
    if field_key == "assignee_id":
        return task.assignee_id != incoming_value
    if field_key == "model_name":
        return _normalize_model_name(task.model_name) != _normalize_model_name(incoming_value)
    return True


def _check_executing_mutation_lock(task, payload):
    if task.status != TaskStatus.EXECUTING:
        return None
    attempted = []
    for key, label in EXECUTING_LOCKED_MUTATION_FIELDS.items():
        if key not in payload:
            continue
        if _is_locked_field_change(task, key, payload.get(key)):
            attempted.append(label)
    if not attempted:
        return None
    return _executing_lock_response(task, attempted)


def _ensure_model_on_user(user, model_name, description=""):
    """Auto-add a model to a user's available_models if not already present.

    Looks up pricing from agent_models.json so the frontend can compute costs
    without depending on seedmodels having been run.
    """
    if not model_name or not user:
        return
    existing_names = {m["name"] for m in user.available_models if isinstance(m, dict)}
    if model_name not in existing_names:
        from .pricing import get_pricing_table
        pricing = get_pricing_table()
        model_entry = {"name": model_name, "description": description, "is_default": False}
        if model_name in pricing:
            model_entry["input_price_per_1m_tokens"] = pricing[model_name]["input_price_per_1m_tokens"]
            model_entry["output_price_per_1m_tokens"] = pricing[model_name]["output_price_per_1m_tokens"]
            model_entry["cache_read_price_per_1m_tokens"] = pricing[model_name].get("cache_read_price_per_1m_tokens")
        user.available_models = list(user.available_models) + [model_entry]
        user.save(update_fields=["available_models"])


def _agent_key(user):
    """Canonical agent name from an agent user's email (strips the +model
    suffix in identity emails like ``glm+zai-coding-plan/glm-5.2@odin.agent``)."""
    if not user or not user.email:
        return ""
    return user.email.split("@")[0].split("+")[0].lower()


def _default_model_for_user(user):
    """Default model for a user, agent_models.json first (single source, F45).

    Resolution: active-lineup default (agent_models.json) → is_default entry in
    the user's available_models → first available_models entry → the user's
    default_model column. None for users with no model data (e.g. humans).
    """
    if not user:
        return None
    from .pricing import get_agent_default_model

    model = get_agent_default_model(_agent_key(user))
    if model:
        return model
    models = [m for m in (user.available_models or []) if isinstance(m, dict) and m.get("name")]
    if not models:
        return user.default_model or None
    for model in models:
        if model.get("is_default"):
            return model["name"]
    return models[0]["name"]


def _ensure_board_membership(board, user):
    """Auto-add a user to a board if they aren't already a member."""
    if user is not None:
        BoardMembership.objects.get_or_create(board=board, user=user)


def _record_change(histories, task, field_name, old_value, new_value, changed_by):
    """Record a field change if old != new. Appends to histories list."""
    old_str = str(old_value) if old_value is not None else ""
    new_str = str(new_value) if new_value is not None else ""
    if old_str != new_str:
        histories.append(TaskHistory(
            task=task, schedule_run=task.current_schedule_run, field_name=field_name,
            old_value=old_str, new_value=new_str,
            changed_by=changed_by,
        ))
        return True
    return False


def _normalize_dependency_ids(depends_on):
    normalized = []
    seen = set()
    for raw in depends_on or []:
        value = str(raw).strip()
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _build_dependency_graph(board, pending_task_id=None, pending_depends_on=None):
    graph = {
        str(task.id): _normalize_dependency_ids(task.depends_on)
        for task in Task.objects.filter(board=board).only("id", "depends_on")
    }
    if pending_task_id is not None:
        graph[str(pending_task_id)] = _normalize_dependency_ids(pending_depends_on)
    return graph


def _would_create_dependency_cycle(board, task_id, depends_on):
    task_key = str(task_id)
    target_deps = set(_normalize_dependency_ids(depends_on))
    if task_key in target_deps:
        return True

    graph = _build_dependency_graph(board, pending_task_id=task_key, pending_depends_on=depends_on)
    queue = deque(target_deps)
    visited = set()

    while queue:
        current = queue.popleft()
        if current == task_key:
            return True
        if current in visited:
            continue
        visited.add(current)
        queue.extend(graph.get(current, []))
    return False


def _validate_dependency_selection(board, depends_on, task=None):
    normalized = _normalize_dependency_ids(depends_on)
    if task and str(task.id) in normalized:
        raise ValidationError({"depends_on": "Task cannot depend on itself."})

    if not normalized:
        return normalized

    existing_ids = set(_normalize_dependency_ids(task.depends_on if task else []))
    dep_map = {
        str(dep.id): dep
        for dep in Task.objects.filter(id__in=normalized).only("id", "board_id", "status")
    }

    missing = [dep_id for dep_id in normalized if dep_id not in dep_map]
    if missing:
        raise ValidationError({"depends_on": f"Dependency task(s) not found: {', '.join(missing)}"})

    invalid_board = [dep_id for dep_id, dep in dep_map.items() if dep.board_id != board.id]
    if invalid_board:
        raise ValidationError({"depends_on": "Dependencies must belong to the same board."})

    invalid_statuses = []
    for dep_id in normalized:
        if dep_id in existing_ids:
            continue
        dep = dep_map[dep_id]
        if dep.status not in {TaskStatus.TODO, TaskStatus.IN_PROGRESS}:
            invalid_statuses.append(f"{dep_id}:{dep.status}")
    if invalid_statuses:
        raise ValidationError({
            "depends_on": (
                "New dependencies must be in TODO or IN_PROGRESS. "
                f"Invalid: {', '.join(invalid_statuses)}"
            )
        })

    if task and _would_create_dependency_cycle(board, task.id, normalized):
        raise ValidationError({"depends_on": "Dependency update would create a cycle."})

    return normalized

class StandardPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 200


class HistoryPagination(StandardPagination):
    page_size = 100


class CommentPagination(StandardPagination):
    page_size = 25


def _parse_multi_values(query_params, key, aliases=()):
    values = []
    for param_key in (key, *aliases):
        values.extend(query_params.getlist(param_key))
    if not values:
        raw = query_params.get(key)
        if raw:
            values = [raw]
    parsed = []
    for value in values:
        parsed.extend([v.strip() for v in str(value).split(",") if v.strip()])
    return parsed


def _parse_iso_datetime(value, end_of_day=False):
    if not value:
        return None
    d = parse_date(value)
    if d is not None:
        dt = datetime.combine(d, time.max if end_of_day else time.min)
        return timezone.make_aware(dt, timezone.get_current_timezone())
    dt = parse_datetime(value)
    if dt is not None:
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        return dt
    raise ValidationError({"date": f"Invalid datetime/date value: {value}. Use ISO-8601 date or datetime."})


def _apply_date_range(qs, query_params, field_name, from_key, to_key):
    from_raw = query_params.get(from_key)
    to_raw = query_params.get(to_key)
    if from_raw:
        qs = qs.filter(**{f"{field_name}__gte": _parse_iso_datetime(from_raw)})
    if to_raw:
        qs = qs.filter(**{f"{field_name}__lte": _parse_iso_datetime(to_raw, end_of_day=True)})
    return qs


def _exclude_hidden_scheduled_tasks(qs, hide_terminal_recurring=True):
    # Only FUTURE occurrences hide from the board (they live on the
    # Scheduling page until they fire). Once a scheduled task has run,
    # it is board history like any other task — hiding executed runs
    # made the daily ops invisible on kanban (user report, task 301).
    qs = qs.exclude(
        schedule_id__isnull=False,
        schedule__status__in=[ScheduleStatus.ACTIVE, ScheduleStatus.PAUSED],
        status__in=[TaskStatus.BACKLOG, TaskStatus.TODO],
    )
    # Board hygiene: terminal tasks (DONE/TESTING) from RECURRING schedules
    # collapse into the schedule's run history. With fresh-task-per-release
    # each daily run mints its own task — without this filter months of
    # finished daily tasks would drown the kanban. The tasks still exist
    # (accessible by id, via the schedule's runs, or through an explicit
    # status filter); they're just not pinned to the board listing.
    if hide_terminal_recurring:
        qs = qs.exclude(
            schedule_id__isnull=False,
            schedule__kind=ScheduleKind.RECURRING,
            status__in=TERMINAL_SUCCESS_STATUSES,
        )
    return qs


def _parse_sort_tokens(sort_raw, allowed_fields, default_tokens):
    if not sort_raw:
        return default_tokens
    tokens = []
    for part in sort_raw.split(","):
        token = part.strip()
        if not token:
            continue
        descending = token.startswith("-")
        field = token[1:] if descending else token
        if field not in allowed_fields:
            raise ValidationError({"sort": f"Unsupported sort field: {field}"})
        tokens.append((field, descending))
    return tokens or default_tokens


def _build_order_by(tokens, allowed_map):
    order_by = []
    for field, descending in tokens:
        mapped = allowed_map[field]
        if callable(mapped):
            order_by.append(mapped(descending))
        else:
            order_by.append(f"-{mapped}" if descending else mapped)
    return order_by


class UserViewSet(viewsets.ModelViewSet):
    serializer_class = UserSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        qs = User.objects.all().annotate(task_count=Count("tasks", distinct=True))
        query_params = self.request.query_params

        search = query_params.get("search")
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(email__icontains=search))

        roles = _parse_multi_values(query_params, "role")
        if roles:
            qs = qs.filter(role__in=roles)

        board_ids = _parse_multi_values(query_params, "board_id", aliases=("board",))
        if board_ids:
            qs = qs.filter(board_memberships__board_id__in=board_ids)

        # Members page join date is users.created_at.
        qs = _apply_date_range(qs, query_params, "created_at", "joined_from", "joined_to")
        qs = _apply_date_range(qs, query_params, "created_at", "created_from", "created_to")
        qs = _apply_date_range(qs, query_params, "updated_at", "updated_from", "updated_to")

        tokens = _parse_sort_tokens(
            query_params.get("sort"),
            {"name", "created_at", "task_count"},
            default_tokens=[("name", False)],
        )
        qs = qs.order_by(*_build_order_by(tokens, {
            "name": "name",
            "created_at": "created_at",
            "task_count": "task_count",
        }))
        return qs

    def get_serializer_class(self):
        if self.action == "list":
            return MemberListSerializer
        return UserSerializer

    def get_permissions(self):
        if self.action in ("create", "destroy"):
            return [IsAdmin()]
        return []

    def update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return super().update(request, *args, **kwargs)


def enroll_all_active_agents(board, disabled_agents=None):
    """Enroll every active AGENT User on ``board`` (W10.4 default-on roster).

    Single source of truth for "this agent is allowed on this board".
    Idempotent — re-running produces zero new rows (ignore_conflicts=True
    on the underlying BoardMembership unique constraint). Used by:

      * ``BoardViewSet.create`` — every fresh board, regardless of whether
        the user supplied a working directory or asked for ``auto_init``.
        Previous default (only the base/CLAUDE agent joined a new board)
        was the symptom board 6 hit: cheap agents sat idle until the
        operator learned the curl PATCH path.
      * ``BoardViewSet._create_agent_memberships`` — kept for the
        init-odin subprocess path.

    ``disabled_agents`` is the explicit opt-out: agents whose ``name``
    appears in this list are skipped. Unknown names are ignored (they
    aren't active; check is by User.name, not email).

    Retired agents (``User.is_active=False``) are always skipped — their
    rows persist for FK integrity (task #135) but must not join a board.

    Returns the queryset of newly created memberships (may be empty when
    every active agent was already enrolled or all were in ``disabled_agents``).
    """
    disabled_set = set(disabled_agents or [])
    agent_users = User.objects.filter(role=UserRole.AGENT, is_active=True)

    # Skip anyone already enrolled on this board so the function is a
    # no-op for the backfill helper use case.
    already_ids = set(
        BoardMembership.objects.filter(
            board=board, user__in=agent_users,
        ).values_list("user_id", flat=True)
    )

    new_memberships = []
    for user in agent_users:
        if user.id in already_ids:
            continue
        if user.name in disabled_set:
            continue
        new_memberships.append(BoardMembership(board=board, user=user))

    if new_memberships:
        BoardMembership.objects.bulk_create(new_memberships, ignore_conflicts=True)
    return new_memberships


class BoardViewSet(viewsets.ModelViewSet):
    serializer_class = BoardSerializer
    pagination_class = StandardPagination

    # System directories that must never be used as working_dir
    _BLOCKED_DIRS = frozenset({
        "/", "/bin", "/sbin", "/usr", "/usr/bin", "/usr/sbin",
        "/etc", "/var", "/tmp", "/dev", "/proc", "/sys",
        "/System", "/Library", "/Applications",
    })

    def get_queryset(self):
        qs = Board.objects.prefetch_related("memberships").annotate(
            member_count=Count("memberships", filter=Q(memberships__user__role__in=["HUMAN", "ADMIN"]), distinct=True),
            task_count=Count("tasks", distinct=True),
        )
        query_params = self.request.query_params

        search = query_params.get("search")
        if search:
            if search.isdigit():
                qs = qs.filter(Q(name__icontains=search) | Q(description__icontains=search) | Q(id=search))
            else:
                qs = qs.filter(Q(name__icontains=search) | Q(description__icontains=search))

        qs = _apply_date_range(qs, query_params, "created_at", "created_from", "created_to")
        qs = _apply_date_range(qs, query_params, "updated_at", "updated_from", "updated_to")

        tokens = _parse_sort_tokens(
            query_params.get("sort"),
            {"id", "name", "created_at", "updated_at", "member_count", "task_count"},
            default_tokens=[("created_at", True)],
        )
        qs = qs.order_by(*_build_order_by(tokens, {
            "id": "id",
            "name": "name",
            "created_at": "created_at",
            "updated_at": "updated_at",
            "member_count": "member_count",
            "task_count": "task_count",
        }))
        return qs

    def get_serializer_class(self):
        if self.action == "list":
            return BoardListSerializer
        if self.action == "create":
            return CreateBoardSerializer
        return BoardSerializer

    def retrieve(self, request, *args, **kwargs):
        board = get_object_or_404(
            Board.objects.prefetch_related(
                "tasks__assignee", "tasks__labels", "memberships"
            ),
            pk=self.kwargs["pk"],
        )
        return Response(BoardDetailSerializer(board).data)

    def create(self, request, *args, **kwargs):
        ser = CreateBoardSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        directory_mode = ser.validated_data.pop("directory_mode", "existing")
        parent_directory = ser.validated_data.pop("parent_directory", None)
        directory_name = ser.validated_data.pop("directory_name", None)
        working_dir = ser.validated_data.pop("working_dir", None)
        auto_init = ser.validated_data.pop("auto_init", True)
        disabled_agents = ser.validated_data.pop("disabled_agents", [])

        if directory_mode == "create":
            working_dir = self._create_working_dir(parent_directory, directory_name)
        elif working_dir:
            self._validate_working_dir(working_dir)

        board = Board.objects.create(**ser.validated_data, working_dir=working_dir)

        # Every new board ships with every active AGENT User enabled — the
        # operator no longer has to learn the curl PATCH path to make the
        # cheap-tier agents visible to the planner. The opt-out list still
        # wins for callers who want fewer agents on this specific board.
        enroll_all_active_agents(board, disabled_agents=disabled_agents)

        if working_dir and auto_init:
            self._init_odin_for_board(board, disabled_agents=disabled_agents)

        return Response(BoardSerializer(board).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        board = self.get_object()

        # Block working_dir changes after creation
        new_working_dir = request.data.get("working_dir")
        if new_working_dir is not None and new_working_dir != board.working_dir:
            raise ValidationError({
                "working_dir": "Cannot change working directory after board creation. "
                               "Create a new board instead."
            })

        # Let DRF handle remaining fields
        return super().update(request, *args, **kwargs)

    def _resolve_child_path(self, parent_directory, directory_name):
        normalized_name = _normalize_directory_name(directory_name)
        try:
            parent_resolved = Path(os.path.expanduser(parent_directory)).resolve()
        except (ValueError, OSError) as exc:
            raise ValidationError({"parent_directory": f"Invalid path: {exc}"})

        if not parent_resolved.is_absolute():
            raise ValidationError({"parent_directory": "Path must be absolute."})

        if str(parent_resolved) in self._BLOCKED_DIRS:
            raise ValidationError({"parent_directory": "System directories cannot be used as project directories."})

        if not parent_resolved.exists():
            raise ValidationError({"parent_directory": f"Directory does not exist: {parent_resolved}"})

        if not parent_resolved.is_dir():
            raise ValidationError({"parent_directory": f"Path is not a directory: {parent_resolved}"})

        if not os.access(str(parent_resolved), os.W_OK):
            raise ValidationError({"parent_directory": f"Directory is not writable: {parent_resolved}"})

        child_path = (parent_resolved / normalized_name).resolve()
        if child_path.parent != parent_resolved:
            raise ValidationError({"directory_name": "Directory name must stay within the selected parent directory."})
        if str(child_path) in self._BLOCKED_DIRS:
            raise ValidationError({"directory_name": "System directories cannot be used as project directories."})
        if child_path.exists():
            raise ValidationError({"directory_name": f"Directory already exists: {child_path}"})
        if Board.objects.filter(working_dir=str(child_path)).exists():
            existing = Board.objects.filter(working_dir=str(child_path)).first()
            raise ValidationError({
                "directory_name": f'Already linked to board "{existing.name}" (ID {existing.id}).'
            })
        return child_path

    def _create_working_dir(self, parent_directory, directory_name):
        child_path = self._resolve_child_path(parent_directory, directory_name)
        try:
            child_path.mkdir(parents=False, exist_ok=False)
        except FileExistsError:
            raise ValidationError({"directory_name": f"Directory already exists: {child_path}"})
        except PermissionError:
            raise ValidationError({"parent_directory": f"Permission denied creating directory: {child_path}"})
        except OSError as exc:
            raise ValidationError({"directory_name": f"Failed to create directory: {exc}"})
        return str(child_path)

    def _validate_working_dir(self, path_str, exclude_board_id=None):
        """Validate a working directory path."""
        try:
            resolved = Path(os.path.expanduser(path_str)).resolve()
        except (ValueError, OSError) as exc:
            raise ValidationError({"working_dir": f"Invalid path: {exc}"})

        if not resolved.is_absolute():
            raise ValidationError({"working_dir": "Path must be absolute."})

        if str(resolved) in self._BLOCKED_DIRS:
            raise ValidationError({"working_dir": "System directories cannot be used as project directories."})

        if not resolved.exists():
            raise ValidationError({"working_dir": f"Directory does not exist: {resolved}"})

        if not resolved.is_dir():
            raise ValidationError({"working_dir": f"Path is not a directory: {resolved}"})

        if not os.access(str(resolved), os.W_OK):
            raise ValidationError({"working_dir": f"Directory is not writable: {resolved}"})

        # Uniqueness check (DB UNIQUE handles it too, but this gives a better error)
        qs = Board.objects.filter(working_dir=str(resolved))
        if exclude_board_id:
            qs = qs.exclude(id=exclude_board_id)
        existing = qs.first()
        if existing:
            raise ValidationError({
                "working_dir": f'Already linked to board "{existing.name}" (ID {existing.id}).'
            })

    def _check_odin_initialized(self, path_str):
        """Check if .odin/config.yaml exists at path."""
        if not path_str:
            return False
        config_path = Path(path_str) / ".odin" / "config.yaml"
        return config_path.is_file()

    def _get_taskit_base_url(self):
        """Resolve the TaskIt API base URL from settings or request."""
        base = getattr(settings, "TASKIT_BASE_URL", None)
        if base:
            return base
        request = self.request
        return f"{request.scheme}://{request.get_host()}"

    def _init_odin_for_board(self, board, disabled_agents=None):
        """Run `odin init --board-id --base-url` in the board's working_dir."""
        if not board.working_dir:
            return

        cli_path = getattr(settings, "ODIN_CLI_PATH", "odin")
        base_url = self._get_taskit_base_url()
        cmd = [
            cli_path, "init", "--force",
            "--board-id", str(board.id),
            "--base-url", base_url,
        ]

        try:
            result = subprocess.run(
                cmd,
                cwd=board.working_dir,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                logger.warning(
                    "odin init failed for board %s (exit=%s): %s",
                    board.id, result.returncode, result.stderr[:500],
                )
                return
        except FileNotFoundError:
            logger.warning("odin CLI not found at '%s' — board %s created without init", cli_path, board.id)
            return
        except subprocess.TimeoutExpired:
            logger.warning("odin init timed out for board %s", board.id)
            return
        except Exception:
            logger.exception("Failed to run odin init for board %s", board.id)
            return

        board.odin_initialized = True
        board.save(update_fields=["odin_initialized"])

        # Create BoardMembership records from agent Users in DB (populated by seedmodels)
        self._create_agent_memberships(board, disabled_agents=disabled_agents)

    def _create_agent_memberships(self, board, disabled_agents=None):
        """Create BoardMembership records for all active agent Users, skipping disabled ones.

        Idempotent — calling twice produces no duplicates (bulk_create uses
        ignore_conflicts=True). Wraps the public helper so the init-odin
        path stays a one-liner.
        """
        enroll_all_active_agents(board, disabled_agents=disabled_agents)

        # Best-effort seeding: populate reviewer_order with the strongest-
        # first ordering derived from the agents enabled at creation time.
        # Not load-bearing — select_reviewer_by_context_size's runtime
        # fallback already produces identical behavior for a board with an
        # empty reviewer_order, so this only pins the deterministic order
        # up-front for a fresh board.
        if not board.reviewer_order:
            by_agent = _board_enabled_agents(board)
            order = _strongest_first_reviewer_order(by_agent)
            if order:
                board.reviewer_order = order
                board.save(update_fields=["reviewer_order"])

    @action(detail=False, methods=["get"], url_path="check-dir")
    def check_dir(self, request, *args, **kwargs):
        """Pre-flight check whether a directory can be used for a new board."""
        mode = (request.query_params.get("mode") or "existing").strip().lower()
        if mode not in {"existing", "create"}:
            return Response({"error": "mode must be 'existing' or 'create'"}, status=status.HTTP_400_BAD_REQUEST)

        result = {
            "odin_exists": False,
            "linked_board": None,
            "can_init": False,
            "message": "",
            "resolved_path": "",
        }

        if mode == "create":
            parent_directory = request.query_params.get("parent_directory", "").strip()
            directory_name = request.query_params.get("directory_name", "").strip()
            if not parent_directory or not directory_name:
                return Response({"error": "parent_directory and directory_name query params required"}, status=status.HTTP_400_BAD_REQUEST)
            try:
                resolved = self._resolve_child_path(parent_directory, directory_name)
            except ValidationError as exc:
                if isinstance(exc.detail, dict):
                    first_value = next(iter(exc.detail.values()))
                    if isinstance(first_value, (list, tuple)):
                        result["message"] = str(first_value[0])
                    else:
                        result["message"] = str(first_value)
                else:
                    result["message"] = str(exc.detail)
                return Response(result)
            result["resolved_path"] = str(resolved)
            result["can_init"] = True
            result["message"] = "Ready to create and initialize directory."
            return Response(result)

        path = request.query_params.get("path", "").strip()
        if not path:
            return Response({"error": "path query param required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            resolved = Path(os.path.expanduser(path)).resolve()
        except (ValueError, OSError):
            result["message"] = "Invalid path."
            return Response(result)

        if not resolved.exists():
            result["message"] = "Directory does not exist."
            return Response(result)

        if not resolved.is_dir():
            result["message"] = "Path is not a directory."
            return Response(result)

        # Check .odin/config.yaml on disk
        config_path = resolved / ".odin" / "config.yaml"
        result["odin_exists"] = config_path.is_file()

        # Check DB for existing board with this working_dir
        existing = Board.objects.filter(working_dir=str(resolved)).first()
        if existing:
            result["linked_board"] = {"id": existing.id, "name": existing.name}
            result["message"] = f'Already linked to board "{existing.name}" (ID {existing.id}).'
            return Response(result)

        result["resolved_path"] = str(resolved)
        result["can_init"] = True
        if result["odin_exists"]:
            result["message"] = "Odin config exists. Will be overwritten on init."
        else:
            result["message"] = "Ready for initialization."
        return Response(result)

    @action(detail=True, methods=["post"], url_path="init-odin")
    def init_odin(self, request, *args, **kwargs):
        """Initialize odin in the board's working directory."""
        board = self.get_object()
        if not board.working_dir:
            raise ValidationError({"detail": "Board has no working directory set."})
        if board.odin_initialized:
            raise ValidationError({"detail": "Odin is already initialized for this board."})

        self._init_odin_for_board(board)
        board.refresh_from_db()
        return Response(BoardSerializer(board).data)

    @action(detail=True, methods=["post"])
    def link(self, request, *args, **kwargs):
        """Link a board to an existing project directory with .odin/config.yaml."""
        board = self.get_object()
        working_dir = request.data.get("working_dir")
        if not working_dir:
            raise ValidationError({"working_dir": "This field is required."})

        self._validate_working_dir(working_dir, exclude_board_id=board.id)

        config_path = Path(working_dir) / ".odin" / "config.yaml"
        if not config_path.is_file():
            raise ValidationError({"working_dir": "No .odin/config.yaml found in this directory. Run 'odin init' first or use the init-odin endpoint."})

        try:
            config = yaml.safe_load(config_path.read_text()) or {}
        except Exception:
            raise ValidationError({"working_dir": "Failed to read .odin/config.yaml."})

        existing_board_id = config.get("board_id")
        if existing_board_id and existing_board_id != board.id:
            raise ValidationError({
                "working_dir": f"This directory's .odin/config.yaml references board {existing_board_id}, not this board ({board.id})."
            })

        # Update config to point to this board
        config["board_id"] = board.id
        config["base_url"] = self._get_taskit_base_url()
        try:
            config_path.write_text(yaml.dump(config, default_flow_style=False))
        except Exception:
            raise ValidationError({"working_dir": "Failed to update .odin/config.yaml."})

        board.working_dir = str(Path(working_dir).resolve())
        board.odin_initialized = True
        board.save(update_fields=["working_dir", "odin_initialized", "updated_at"])
        return Response(BoardSerializer(board).data)

    @action(detail=True, methods=["post"], url_path="claude-token")
    def claude_token(self, request, *args, **kwargs):
        """Write the board's Claude Code OAuth token to <working_dir>/.claude-token.

        The token is stored write-only as a 0600 file the odin microsandbox harness
        reads (never returned to the client, never committed — .claude-token is
        gitignored). Obtain it on the host with `claude setup-token`.
        """
        board = self.get_object()
        if not board.working_dir:
            raise ValidationError({"detail": "Board has no working directory. Link a project first."})

        ser = SetClaudeTokenSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        token = ser.validated_data["token"]

        token_path = Path(board.working_dir) / ".claude-token"
        try:
            token_path.write_text(token + "\n")
            token_path.chmod(0o600)
        except OSError as exc:
            raise ValidationError({"detail": f"Failed to write .claude-token: {exc}"})

        return Response(BoardSerializer(board).data)

    @action(detail=True, methods=["get"], url_path="members", url_name="members-list")
    def members(self, request, *args, **kwargs):
        """List board members."""
        board = self.get_object()
        user_ids = board.memberships.values_list("user_id", flat=True)
        users = User.objects.filter(id__in=user_ids)
        return Response(UserSerializer(users, many=True).data)

    @action(detail=True, methods=["post"], url_path="members/add", url_name="members-add")
    def members_add(self, request, *args, **kwargs):
        """Bulk add members to a board."""
        board = self.get_object()
        ser = BoardMemberIdsSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        user_ids = ser.validated_data["user_ids"]
        users = User.objects.filter(id__in=user_ids)
        memberships = [
            BoardMembership(board=board, user=user)
            for user in users
        ]
        BoardMembership.objects.bulk_create(memberships, ignore_conflicts=True)
        return Response(BoardSerializer(board).data)

    @action(detail=True, methods=["post"], url_path="members/remove", url_name="members-remove")
    def members_remove(self, request, *args, **kwargs):
        """Bulk remove members from a board. Unassigns their tasks and records history."""
        board = self.get_object()
        ser = BoardMemberIdsSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        user_ids = ser.validated_data["user_ids"]

        # Unassign tasks belonging to removed members on this board
        affected_tasks = Task.objects.filter(board=board, assignee_id__in=user_ids)
        histories = []
        for task in affected_tasks:
            histories.append(TaskHistory(
                task=task,
                field_name="assignee_id",
                old_value=str(task.assignee_id),
                new_value="",
                changed_by="system@taskit",
            ))
            task.assignee = None
        Task.objects.bulk_update(affected_tasks, ["assignee"])
        TaskHistory.objects.bulk_create(histories)

        # Remove memberships
        BoardMembership.objects.filter(board=board, user_id__in=user_ids).delete()
        return Response(BoardSerializer(board).data)

    @action(detail=True, methods=["get"], url_path="agents", url_name="agents-list")
    def agents(self, request, *args, **kwargs):
        """List agents for this board from DB (BoardMembership + User records)."""
        board = self.get_object()
        memberships = BoardMembership.objects.filter(
            board=board, user__role="AGENT",
        ).select_related("user")

        all_agents = User.objects.filter(role="AGENT", is_active=True)
        member_user_ids = {m.user_id for m in memberships}

        agents = []
        for agent_user in all_agents:
            # Find membership if exists
            membership = next((m for m in memberships if m.user_id == agent_user.id), None)
            disabled = set(membership.disabled_models or []) if membership else set()

            models_list = []
            for m in agent_user.available_models:
                if isinstance(m, dict):
                    model_name = m.get("name", "")
                    models_list.append({
                        "name": model_name,
                        "enabled": model_name not in disabled,
                        "is_default": m.get("is_default", False),
                        "description": m.get("description", ""),
                    })
            agents.append({
                "name": agent_user.name,
                "enabled": agent_user.id in member_user_ids,
                "cli_command": agent_user.cli_command,
                "capabilities": agent_user.capabilities or [],
                "cost_tier": agent_user.cost_tier or "medium",
                "default_model": agent_user.default_model,
                "premium_model": agent_user.premium_model,
                "models": models_list,
            })

        return Response({"agents": agents})

    @action(detail=True, methods=["patch"], url_path=r"agents/(?P<agent_name>[^/.]+)", url_name="agents-toggle")
    def agents_toggle(self, request, *args, **kwargs):
        """Toggle an agent's enabled status by creating/deleting BoardMembership."""
        board = self.get_object()
        agent_name = kwargs.get("agent_name")

        email = f"{agent_name}@odin.agent"
        # is_active=True filters retired agents (qwen/gemini as of task #135)
        # whose User rows persist for FK integrity but aren't in the active
        # lineup and can't be toggled onto a board.
        agent_user = User.objects.filter(
            email=email, role="AGENT", is_active=True,
        ).first()
        if not agent_user:
            return Response(
                {"detail": f"Agent '{agent_name}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        enabled = request.data.get("enabled")
        if not isinstance(enabled, bool):
            raise ValidationError({"enabled": "Must be a boolean."})

        if enabled:
            BoardMembership.objects.get_or_create(board=board, user=agent_user)
        else:
            # Unassign tasks before removing membership
            affected_tasks = Task.objects.filter(board=board, assignee=agent_user)
            histories = []
            for task in affected_tasks:
                histories.append(TaskHistory(
                    task=task,
                    field_name="assignee_id",
                    old_value=str(task.assignee_id),
                    new_value="",
                    changed_by="system@taskit",
                ))
                task.assignee = None
            Task.objects.bulk_update(affected_tasks, ["assignee"])
            if histories:
                TaskHistory.objects.bulk_create(histories)
            BoardMembership.objects.filter(board=board, user=agent_user).delete()

        return Response({
            "name": agent_name,
            "enabled": enabled,
            "board": BoardSerializer(board).data,
        })

    @action(detail=True, methods=["get"], url_path="agent-stats", url_name="agent-stats")
    def agent_stats(self, request, *args, **kwargs):
        """Return per-agent success-rate + median cost for odin's history-driven routing.

        Each row has the same shape as ``tasks.agent_stats.AgentStats``
        and is consumed directly by ``odin.agent_routing.stats_from_dicts``.
        Agents with zero observed merges are omitted — the suggester
        treats them as having thin history (the static fallback path).
        """
        board = self.get_object()
        from .agent_stats import compute_agent_stats_for_board

        spec_id = request.query_params.get("spec")
        spec = None
        if spec_id is not None:
            try:
                spec = Spec.objects.get(pk=spec_id, board=board)
            except (Spec.DoesNotExist, ValueError):
                return Response(
                    {"detail": f"Spec '{spec_id}' not found on this board."},
                    status=status.HTTP_404_NOT_FOUND,
                )

        stats = compute_agent_stats_for_board(board, spec=spec)
        # Dataclass -> dict for the JSON serializer. Stable ordering by
        # agent name so the operator-facing table doesn't shuffle.
        rows = [s.to_dict() for s in stats.values()]
        rows.sort(key=lambda r: (r["sample_count"] == 0, r["name"]))
        return Response({"agents": rows})

    @action(detail=True, methods=["get"], url_path="league", url_name="league")
    def league(self, request, *args, **kwargs):
        """Per (agent, model) league table over landed tasks (DONE + TESTING).

        Query params:
            since_spec — odin_id of the cutoff spec; include tasks whose
                spec was created at or after that spec. Returns empty rows
                when the named spec is not on this board.
        """
        board = self.get_object()
        from .league import compute_league_for_board

        since_spec = request.query_params.get("since_spec") or None

        rows = compute_league_for_board(board, since_spec=since_spec)
        row_dicts = [r.to_dict() for r in rows]
        return Response({
            "rows": row_dicts,
            "meta": {
                "board_id": board.id,
                "task_count": sum(r["tasks_landed"] for r in row_dicts),
                "since_spec": since_spec,
            },
        })

    @action(detail=True, methods=["get"], url_path="routing-config", url_name="routing-config")
    def routing_config(self, request, *args, **kwargs):
        """Return full agent/model routing config for odin planning and execution."""
        board = self.get_object()
        memberships = BoardMembership.objects.filter(
            board=board, user__role="AGENT",
        ).select_related("user")

        agents = []
        for membership in memberships:
            user = membership.user
            disabled = set(membership.disabled_models or [])
            models_list = []
            for m in user.available_models:
                if not isinstance(m, dict):
                    continue
                model_name = m.get("name", "")
                models_list.append({
                    "name": model_name,
                    "enabled": model_name not in disabled,
                    "is_default": m.get("is_default", False),
                    "description": m.get("description", ""),
                })
            agents.append({
                "name": user.name,
                "cost_tier": user.cost_tier or "medium",
                "capabilities": user.capabilities or [],
                "default_model": user.default_model,
                "premium_model": user.premium_model,
                "models": models_list,
            })

        # task #328: surface the effective routing policy so the web
        # settings "Routing" section renders the real per-failure-class
        # actions, the standing preference order, and the escalation tiers
        # — not a hardcoded guess. Board overrides are already merged in.
        from .failure_policy import (
            effective_policy_table,
            effective_preference_order,
            capability_policy,
            DEFAULT_PREFERENCE_ORDER,
        )
        cap = capability_policy(board)
        routing_policy = {
            "preference_order": effective_preference_order(board),
            "default_preference_order": list(DEFAULT_PREFERENCE_ORDER),
            "capability_escalate_after": cap.escalate_after,
            "capability_escalation_enabled": cap.enabled,
            "capability_max_escalations": cap.max_escalations,
            "failure_actions": effective_policy_table(board),
            "escalation_tiers": board.model_escalation_priority or [],
            "escalation_enabled": board.escalation_enabled,
        }

        return Response({
            "agents": RoutingAgentSerializer(agents, many=True).data,
            # W10.4: planner can self-direct the operator to the settings
            # page when the roster is empty. The orchestrator embeds this
            # URL into the prompt's "Available agents:" block so a board
            # without enabled agents surfaces "No agents enabled on board
            # N — enable at /api/boards/N/agents/" instead of routing
            # silently to the default fallback.
            "settings_path": _agent_settings_hint(board.id),
            "empty": not agents,
            "routing_policy": routing_policy,
        })

    @action(
        detail=True, methods=["patch"],
        url_path=r"agents/(?P<agent_name>[^/.]+)/models/(?P<model_name>.+)",
        url_name="agents-model-toggle",
    )
    def agents_model_toggle(self, request, *args, **kwargs):
        """Toggle a specific model's enabled state within an agent on this board."""
        board = self.get_object()
        agent_name = kwargs.get("agent_name")
        model_name = kwargs.get("model_name")

        email = f"{agent_name}@odin.agent"
        agent_user = User.objects.filter(email=email, role="AGENT").first()
        if not agent_user:
            return Response(
                {"detail": f"Agent '{agent_name}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        membership = BoardMembership.objects.filter(board=board, user=agent_user).first()
        if not membership:
            return Response(
                {"detail": f"Agent '{agent_name}' is not enabled on this board."},
                status=status.HTTP_404_NOT_FOUND,
            )

        ser = ModelToggleSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        enabled = ser.validated_data["enabled"]

        # Validate the model exists on this agent
        agent_model_names = [
            m["name"] for m in agent_user.available_models
            if isinstance(m, dict) and "name" in m
        ]
        if model_name not in agent_model_names:
            return Response(
                {"detail": f"Model '{model_name}' not found on agent '{agent_name}'."},
                status=status.HTTP_404_NOT_FOUND,
            )

        disabled = list(membership.disabled_models or [])

        if not enabled:
            # Disabling: check that at least 1 model remains enabled
            if model_name not in disabled:
                disabled.append(model_name)
            enabled_count = sum(1 for m in agent_model_names if m not in disabled)
            if enabled_count < 1:
                raise ValidationError({
                    "enabled": "Cannot disable all models. At least one model must remain enabled."
                })
        else:
            # Enabling: remove from disabled list
            disabled = [m for m in disabled if m != model_name]

        membership.disabled_models = disabled
        membership.save(update_fields=["disabled_models"])

        return Response({
            "agent": agent_name,
            "model": model_name,
            "enabled": enabled,
        })

    @action(detail=True, methods=["get"], url_path="spec-files", url_name="spec-files")
    def spec_files(self, request, *args, **kwargs):
        """List .md spec files from the board's ./specs directory."""
        board = self.get_object()
        working_dir = (board.working_dir or "").strip()
        if not working_dir:
            return Response({"files": []})

        specs_dir = Path(working_dir) / "specs"
        if not specs_dir.is_dir():
            return Response({"files": []})

        files = []
        for p in sorted(specs_dir.iterdir()):
            if p.is_file() and p.suffix.lower() in (".md", ".txt"):
                files.append({"name": p.name, "path": str(p.relative_to(working_dir))})
        return Response({"files": files})

    @action(detail=True, methods=["post"], url_path="spec-files/read", url_name="spec-files-read")
    def spec_files_read(self, request, *args, **kwargs):
        """Read the content of a spec file by relative path."""
        board = self.get_object()
        working_dir = (board.working_dir or "").strip()
        if not working_dir:
            raise ValidationError({"detail": "Board has no working directory set."})

        rel_path = (request.data.get("path") or "").strip()
        if not rel_path:
            raise ValidationError({"path": "File path is required."})

        file_path = (Path(working_dir) / rel_path).resolve()
        # Prevent path traversal outside working_dir
        if not str(file_path).startswith(str(Path(working_dir).resolve())):
            raise ValidationError({"path": "Invalid file path."})
        if not file_path.is_file():
            raise ValidationError({"path": "File not found."})

        content = file_path.read_text(encoding="utf-8", errors="replace")
        return Response({"name": file_path.name, "content": content})

    @action(detail=True, methods=["post"])
    def clear(self, request, *args, **kwargs):
        """Delete all tasks (and their history via cascade) and specs for this board."""
        board = self.get_object()
        tasks_deleted, _ = Task.objects.filter(board=board).delete()
        specs_deleted, _ = Spec.objects.filter(board=board).delete()
        return Response({
            "tasks_deleted": tasks_deleted,
            "specs_deleted": specs_deleted,
        })

    @action(detail=True, methods=["get"], url_path="story")
    def story(self, request, pk=None):
        """Board story: chronological event stream across all specs —
        landings (with hands-free flag), failures, merge conflicts,
        reflection escalations, wave open/close. Shared builder with
        testing_tools/board_story.py (see tasks/board_story.py).
        """
        board = get_object_or_404(Board, pk=pk)
        since = request.query_params.get("since")
        return Response(build_board_story(board, since=since))

    @action(detail=True, methods=["get"], url_path="factory")
    def factory(self, request, pk=None):
        """Factory snapshot: single-call operator view of a board's live
        state — running tasks, queue depths, recent merges, open error
        signatures, and a one-line story headline. See
        tasks/factory_snapshot.py for the assembly logic.
        """
        board = get_object_or_404(Board, pk=pk)
        return Response(build_factory_snapshot(board))

    @action(detail=True, methods=["get"], url_path="inbox")
    def inbox(self, request, pk=None):
        """Board inbox: everything waiting on a human in one call —
        parked merge conflicts, reversibility parks (task #244), the
        TESTING shelf, and open ErrorEvents. See tasks/inbox.py for the
        assembly logic.
        """
        board = get_object_or_404(Board, pk=pk)
        return Response(build_inbox(board))


class ScheduleViewSet(viewsets.ModelViewSet):
    serializer_class = TaskScheduleSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        qs = TaskSchedule.objects.select_related(
            "board", "template_assignee", "materialized_task", "last_released_run"
        ).prefetch_related("runs")
        query_params = self.request.query_params

        board_ids = _parse_multi_values(query_params, "board_id", aliases=("board",))
        if board_ids:
            qs = qs.filter(board_id__in=board_ids)

        statuses = _parse_multi_values(query_params, "status")
        if statuses:
            qs = qs.filter(status__in=statuses)

        kinds = _parse_multi_values(query_params, "kind")
        if kinds:
            qs = qs.filter(kind__in=kinds)

        history_mode = str(query_params.get("history") or "").lower() in ("1", "true", "yes")
        if history_mode:
            qs = qs.filter(
                Q(status__in=[ScheduleStatus.COMPLETED, ScheduleStatus.CANCELED])
                | Q(runs__finished_at_utc__isnull=False)
            ).distinct()
        else:
            qs = qs.exclude(status__in=[ScheduleStatus.COMPLETED, ScheduleStatus.CANCELED])

        # Search by template title or description
        search_term = query_params.get("search") or query_params.get("q")
        if search_term:
            qs = qs.filter(
                Q(template_title__icontains=search_term)
                | Q(template_description__icontains=search_term)
            )

        # Date range filters
        qs = _apply_date_range(qs, query_params, "created_at", "created_from", "created_to")
        qs = _apply_date_range(qs, query_params, "next_run_at_utc", "next_run_from", "next_run_to")

        # Configurable sort
        SCHEDULE_SORT_FIELDS = {
            "next_run_at_utc", "created_at", "template_title", "kind", "status", "starts_at_utc",
        }
        SCHEDULE_SORT_MAP = {f: f for f in SCHEDULE_SORT_FIELDS}
        DEFAULT_SCHEDULE_SORT = [("next_run_at_utc", False)]
        sort_tokens = _parse_sort_tokens(query_params.get("sort"), SCHEDULE_SORT_FIELDS, DEFAULT_SCHEDULE_SORT)
        order_by = _build_order_by(sort_tokens, SCHEDULE_SORT_MAP)
        # Always append "id" as tiebreaker
        if "id" not in order_by and "-id" not in order_by:
            order_by.append("id")

        return qs.order_by(*order_by)

    def create(self, request, *args, **kwargs):
        ser = CreateScheduleSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        board = get_object_or_404(Board, pk=data["board_id"])
        created_by = data.get("created_by", "")
        if data.get("created_by_user_id"):
            created_by = get_object_or_404(User, pk=data["created_by_user_id"]).email
        try:
            starts_at_local = parse_local_datetime(data["starts_at_local"], data["timezone"])
        except ScheduleValidationError as exc:
            raise ValidationError({exc.field: exc.message}) from exc
        schedule = create_schedule(
            board=board,
            kind=data["kind"],
            timezone_name=data["timezone"],
            starts_at_local=starts_at_local,
            template=data["template"],
            recurrence_rule=data.get("recurrence_rule") or {},
            created_by=created_by,
        )
        return Response(TaskScheduleSerializer(schedule).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        schedule = self.get_object()
        ser = UpdateScheduleSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        effective_timezone = data.get("timezone", schedule.timezone)
        try:
            if "starts_at_local" in data:
                effective_starts_at_local = parse_local_datetime(data["starts_at_local"], effective_timezone)
            elif "timezone" in data:
                effective_starts_at_local = rebind_local_datetime(
                    schedule.starts_at_local,
                    schedule.timezone,
                    effective_timezone,
                )
            else:
                effective_starts_at_local = schedule.starts_at_local
        except ScheduleValidationError as exc:
            raise ValidationError({exc.field: exc.message}) from exc
        effective_recurrence = data.get("recurrence_rule", schedule.recurrence_rule or {})
        if "starts_at_local" in data and effective_starts_at_local <= timezone.now().astimezone(effective_starts_at_local.tzinfo):
            raise ValidationError({
                "starts_at_local": "Scheduled start time must be in the future.",
            })
        if schedule.kind == ScheduleKind.RECURRING:
            if effective_recurrence.get("freq") == "WEEKLY":
                weekdays = effective_recurrence.get("by_weekday") or []
                expected = WEEKDAY_KEYS[effective_starts_at_local.weekday()]
                if weekdays and expected not in weekdays:
                    raise ValidationError({
                        "recurrence_rule": f"Weekly recurrence must include the first scheduled weekday: {expected}."
                    })
            if effective_recurrence.get("freq") == "MONTHLY":
                monthdays = effective_recurrence.get("by_monthday") or []
                if monthdays and effective_starts_at_local.day not in monthdays:
                    raise ValidationError({
                        "recurrence_rule": f"Monthly recurrence must include the first scheduled day: {effective_starts_at_local.day}."
                    })
        if "timezone" in data:
            schedule.timezone = effective_timezone
        if any(key in data for key in ("starts_at_local", "timezone")):
            schedule.starts_at_local = effective_starts_at_local
            schedule.starts_at_utc = local_to_utc(schedule.starts_at_local, effective_timezone)
        if "template" in data:
            template = data["template"]
            schedule.template_title = template["title"]
            schedule.template_description = template.get("description", "")
            schedule.template_priority = template.get("priority", schedule.template_priority)
            schedule.template_assignee_id = template.get("assignee_id")
            schedule.template_model_name = template.get("model_name")
            schedule.template_label_ids = template.get("label_ids", [])
            schedule.template_depends_on = template.get("depends_on", [])
            schedule.template_dev_eta_seconds = template.get("dev_eta_seconds")
            schedule.template_spec_id = template.get("spec_id")
            schedule.template_metadata = template.get("metadata", {})
        if "recurrence_rule" in data:
            schedule.recurrence_rule = data["recurrence_rule"]
        if (
            schedule.status in (ScheduleStatus.ACTIVE, ScheduleStatus.PAUSED)
            and any(key in data for key in ("starts_at_local", "timezone", "recurrence_rule"))
        ):
            now = timezone.now()
            schedule.next_run_at_utc = compute_schedule_next_run(schedule, reference_utc=now)
            if schedule.kind == ScheduleKind.ONE_TIME and schedule.next_run_at_utc is None:
                raise ValidationError({
                    "starts_at_local": "Scheduled start time must be in the future.",
                })
            if schedule.status == ScheduleStatus.PAUSED and schedule.next_run_at_utc is None:
                schedule.status = ScheduleStatus.COMPLETED
                schedule.completed_at = now
        schedule.save()
        return Response(TaskScheduleSerializer(schedule).data)

    @action(detail=True, methods=["post"])
    def pause(self, request, pk=None):
        schedule = self.get_object()
        ser = ScheduleStatusMutationSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        schedule.status = ScheduleStatus.PAUSED
        schedule.paused_at = timezone.now()
        schedule.save(update_fields=["status", "paused_at", "updated_at"])
        return Response(TaskScheduleSerializer(schedule).data)

    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None):
        schedule = self.get_object()
        ser = ScheduleStatusMutationSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        now = timezone.now()
        schedule.status = ScheduleStatus.ACTIVE
        schedule.paused_at = None
        schedule.next_run_at_utc = compute_schedule_next_run(schedule, reference_utc=now)
        if schedule.next_run_at_utc is None:
            schedule.status = ScheduleStatus.COMPLETED
            schedule.completed_at = now
            schedule.save(update_fields=["status", "paused_at", "next_run_at_utc", "completed_at", "updated_at"])
            return Response(TaskScheduleSerializer(schedule).data)
        schedule.save(update_fields=["status", "paused_at", "next_run_at_utc", "updated_at"])
        return Response(TaskScheduleSerializer(schedule).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        schedule = self.get_object()
        ser = ScheduleStatusMutationSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        schedule.status = ScheduleStatus.CANCELED
        schedule.canceled_at = timezone.now()
        schedule.next_run_at_utc = None
        schedule.save(update_fields=["status", "canceled_at", "next_run_at_utc", "updated_at"])
        return Response(TaskScheduleSerializer(schedule).data)

    @action(detail=True, methods=["post"])
    def run_now(self, request, pk=None):
        """Create and dispatch one real occurrence of this schedule on demand.

        Builds the occurrence through the same release_schedule_occurrence helper
        the cron tick uses, so the resulting TaskScheduleRun + Task are identical
        to what the next scheduled firing would produce (same board, template
        fields, schedule/current_schedule_run linkage, TaskHistory). The run is
        flagged manual via release_reason and attributed to the request user.

        Guardrails: a manual run never touches cron bookkeeping --
        next_run_at_utc and last_released_run are left exactly as they were, so
        "Run now" can neither shift nor skip the next scheduled firing. Double
        safety comes from reusing the cron path's overlap detection under a
        select_for_update row lock: if the schedule's materialized task is still
        active (i.e. a manual run is already in flight), the request is rejected
        with a 409 rather than creating a second overlapping run.
        """
        schedule = self.get_object()
        requesting_user = _request_task_user(request)
        created_by = requesting_user.email if requesting_user else "manual@taskit"

        with transaction.atomic():
            schedule = (
                TaskSchedule.objects.select_for_update()
                .select_related("materialized_task", "board")
                .get(pk=schedule.pk)
            )

            # Reuse the same overlap concept as the cron path: if the schedule's
            # materialized task is still active, a manual run is already in
            # flight. Reject cleanly with a 4xx instead of silently creating a
            # SKIPPED_OVERLAP run, so two rapid POSTs cannot overlap.
            active = schedule.materialized_task
            if active and active.status in ACTIVE_OVERLAP_STATUSES:
                return Response(
                    {
                        "detail": (
                            f"Schedule {schedule.id} already has an active run "
                            f"(task {active.id}, status {active.status}). Wait for "
                            "it to finish before running again."
                        ),
                        "code": "schedule_run_in_flight",
                        "active_task_id": active.id,
                    },
                    status=status.HTTP_409_CONFLICT,
                )

            now = timezone.now()
            materialized_before = schedule.materialized_task_id
            run, task, outcome = release_schedule_occurrence(
                schedule,
                scheduled_for_utc=now,
                created_by=created_by,
                release_reason=f"Manual run requested by {created_by}.",
                now=now,
            )

            if outcome == "overlap":
                # Lost the race between the pre-check and the release -- another
                # run activated the task. Surface it as a conflict.
                return Response(
                    {
                        "detail": f"Schedule {schedule.id} already has an active run.",
                        "code": "schedule_run_in_flight",
                        "active_task_id": task.id if task else None,
                    },
                    status=status.HTTP_409_CONFLICT,
                )

            # release_schedule_occurrence always mints a fresh task now, so
            # materialized_task always changes -- persist it without touching
            # cron bookkeeping (next_run_at_utc / last_released_run / status).
            if schedule.materialized_task_id != materialized_before:
                schedule.save(update_fields=["materialized_task"])

        return Response(
            {
                "task_id": task.id,
                "run_id": run.id,
                "task": _task_response(task.id),
            },
            status=status.HTTP_200_OK,
        )

    def destroy(self, request, *args, **kwargs):
        schedule = self.get_object()
        schedule.status = ScheduleStatus.CANCELED
        schedule.canceled_at = timezone.now()
        schedule.next_run_at_utc = None
        schedule.save(update_fields=["status", "canceled_at", "next_run_at_utc", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class LabelViewSet(viewsets.ModelViewSet):
    serializer_class = LabelSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        queryset = Label.objects.all().order_by("id")
        board_id = self.request.query_params.get("board_id")
        if board_id:
            queryset = queryset.filter(board_id=board_id)
        return queryset

    def update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return super().update(request, *args, **kwargs)


def _task_response(task_id):
    task = Task.objects.select_related("assignee").prefetch_related("labels").get(
        pk=task_id
    )
    return TaskSerializer(task).data


def _pre_stop_mark(task, target_status, updated_by, reason):
    """Record the user's stop intent in task.metadata BEFORE touching the process.

    This is the lynchpin of the stop flow: if the stop signal races with the
    DAG executor's subprocess monitor, the executor must be able to see these
    flags and honor the user's target status instead of synthesizing FAILED.

    Critically, we also flip active_execution.cancel_requested here — that's
    the exact flag the dag_executor polling loop watches on every 1-second
    poll (see _run_subprocess_with_cancellation in dag_executor.py). Writing
    it here guarantees process termination via os.killpg regardless of
    whether the subsequent _attempt_odin_stop call lands cleanly.

    All guard keys are written in a single atomic save — do not split this
    across multiple .save() calls.
    """
    metadata = dict(task.metadata or {})
    active = dict(metadata.get("active_execution") or {})
    run_token = active.get("run_token")

    # Flip the kill switch the dag_executor polling loop is watching.
    active["cancel_requested"] = True
    metadata["active_execution"] = active

    metadata["ignore_execution_results"] = True
    metadata["pending_stop_target"] = target_status
    metadata["pending_stop_updated_by"] = updated_by
    metadata["pending_stop_reason"] = reason
    metadata["execution_stopped_at"] = timezone.now().isoformat()
    if run_token:
        metadata["stopped_run_token"] = run_token
    task.metadata = metadata
    task.save(update_fields=["metadata", "last_updated_at"])


def _perform_stop_flow(task, target_status, updated_by, reason, force=False):
    """Shared stop-then-transition pipeline for both stop endpoints.

    Returns (response_body, http_status_code). The caller wraps it in a Response.

    Flow:
      1. Write guard metadata (single DB call) — user intent is now authoritative.
      2. Attempt to stop the process. A failure here is NOT fatal; the signal
         may have landed anyway, and even if it didn't, our guard ensures the
         DAG executor will still route the task to the user's target.
      3. Refresh and check status:
         - Still EXECUTING → apply transition ourselves.
         - Already at target_status → DAG executor honored the guard; return current state.
         - Some other status → genuine unexpected transition, return 409.
      4. If the stop command reported an error, surface it as stop_warning
         (non-fatal) on an otherwise successful 200 response.
    """
    _pre_stop_mark(task, target_status, updated_by, reason)

    stop_result = _attempt_odin_stop(task, force=force)

    task.refresh_from_db()
    if task.status != TaskStatus.EXECUTING:
        if task.status == target_status:
            # DAG executor raced ahead and honored the guard. Return the live state.
            body = {"task": _task_response(task.id), "stop": stop_result}
            if not stop_result.get("ok"):
                body["stop_warning"] = stop_result.get("error") or "Stop command reported an error; DAG executor applied the transition."
            return body, status.HTTP_200_OK
        # Unexpected terminal state (e.g., a different path wrote a non-target status).
        return (
            {
                "detail": f"Task moved to {task.status} during stop; expected {target_status}.",
                "stop": stop_result,
            },
            status.HTTP_409_CONFLICT,
        )

    payload = _apply_stop_transition(task, target_status, updated_by, reason, stop_result)
    if not stop_result.get("ok"):
        payload["stop_warning"] = stop_result.get("error") or "Stop command reported an error; transition applied anyway."
    return payload, status.HTTP_200_OK


def _kill_tmux_session_if_present(task):
    """Kill the tmux session hosting the agent, if one exists.

    This is THE essential termination step. odin exec runs CLI agents
    (claude, gemini, codex, glm) inside a *detached* tmux session
    via `tmux new-session -d` — see odin/src/odin/orchestrator.py
    `_execute_via_tmux` and odin/src/odin/tmux.py `launch()`.

    Consequences for the kill path:
      * The tmux server is a separate daemon with its own process group.
      * The agent process lives inside that tmux session, NOT in odin's
        process group.
      * `os.killpg(odin_pid, SIGTERM)` kills only the odin Python wrapper;
        the agent keeps running, posting proof-of-work, making git commits,
        etc., until it finishes naturally.

    odin writes the session name to `task.metadata["tmux_session"]`
    (orchestrator.py:2913). We read it here and issue
    `tmux kill-session -t <name>` to actually stop the work.

    Returns:
      None — no tmux path is applicable (no metadata or tmux not on PATH).
      dict with "ok": bool — tmux kill attempted, result inside.
    """
    import shutil
    import subprocess

    session_name = (task.metadata or {}).get("tmux_session")
    if not session_name:
        return None

    if not shutil.which("tmux"):
        return None

    try:
        result = subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "engine": "tmux",
            "error": f"tmux kill-session timed out for {session_name}",
        }
    except Exception as exc:  # pragma: no cover - unexpected env failure
        logger.exception("tmux kill-session failed for task %s", task.id)
        return {
            "ok": False,
            "engine": "tmux",
            "error": f"tmux kill-session raised: {exc}",
        }

    if result.returncode == 0:
        return {
            "ok": True,
            "engine": "tmux",
            "details": f"Killed tmux session {session_name}.",
        }

    # "can't find session" / "no such session" means the session is already
    # gone — either it finished on its own or someone else killed it. Either
    # way, from our perspective the agent isn't running, so treat as success.
    stderr = (result.stderr or "").strip().lower()
    if "can't find session" in stderr or "no such session" in stderr or "no server running" in stderr:
        return {
            "ok": True,
            "engine": "tmux",
            "details": f"Tmux session {session_name} already gone.",
        }

    return {
        "ok": False,
        "engine": "tmux",
        "error": f"tmux kill-session exit={result.returncode}: {result.stderr.strip()}",
    }


def _attempt_odin_stop(task, force=False):
    """Terminate a running task's agent AND its odin wrapper.

    Two independent kill paths, both attempted:

      1. Tmux session kill (primary) — reads task.metadata["tmux_session"]
         and runs `tmux kill-session`. This is what actually stops the
         agent (claude/gemini/codex/etc.) which runs inside a detached
         tmux session spawned by odin. Killing odin's process group alone
         does NOT reach the agent — see _kill_tmux_session_if_present.

      2. Execution strategy stop (secondary) — kills the odin wrapper
         process group via os.killpg and sets active_execution.cancel_requested
         so the dag_executor polling loop unblocks promptly. Without this,
         the odin wrapper might linger for a few seconds waiting on tmux.

    Overall outcome:
      - ok=True if EITHER path succeeded. Tmux kill alone is sufficient
        to stop the agent's work — the odin wrapper will exit shortly
        after it sees the session is gone. Strategy kill alone is
        sufficient only for the non-tmux execution fallback (rare).
      - ok=False only if both paths were attempted and both failed.

    Legacy fallback: odin CLI `stop` command. Only tried if both primary
    paths fail. Has a known bug (reads metadata["subprocess_pid"] which
    taskit doesn't populate) but harmless to call.

    Belt-and-suspenders: even if this returns ok=False, the dag_executor
    polling loop will pick up active_execution.cancel_requested (written
    by _pre_stop_mark) and kill the odin wrapper within ~1 second.
    """
    from .execution import get_strategy
    from .integrations.odin_runtime import stop_with_odin

    # Step 1: Kill the tmux session hosting the agent. This is the critical
    # step — without it, the agent keeps running regardless of anything else.
    tmux_result = _kill_tmux_session_if_present(task)

    # Step 2: Stop the odin wrapper process group via the execution strategy.
    strategy = get_strategy()
    strategy_result = None
    if strategy:
        strategy_result = strategy.stop(task, force=force)

    tmux_ok = bool(tmux_result and tmux_result.get("ok"))
    strategy_ok = bool(strategy_result and strategy_result.get("ok"))

    if tmux_ok or strategy_ok:
        engines = []
        if tmux_ok:
            engines.append("tmux")
        if strategy_ok:
            engines.append((strategy_result or {}).get("engine", "strategy"))
        return {
            "ok": True,
            "engine": "+".join(engines),
            "details": (tmux_result or {}).get("details") or (strategy_result or {}).get("details", ""),
            "tmux_stop": tmux_result,
            "strategy_stop": strategy_result,
        }

    # Both primary paths failed (or weren't applicable). Last-resort CLI
    # fallback — preserves the old behavior for tmux-based manual workflows.
    cli_result = stop_with_odin(task.id, force=force)
    if cli_result.get("ok"):
        cli_result["engine"] = f"{cli_result.get('engine', 'odin_cli')} (fallback)"
        cli_result["fallback_used"] = True
        cli_result["tmux_stop"] = tmux_result
        cli_result["strategy_stop"] = strategy_result
        return cli_result

    return {
        "ok": False,
        "engine": "tmux+strategy+odin_cli",
        "error": (
            (tmux_result or {}).get("error")
            or (strategy_result or {}).get("error")
            or cli_result.get("error")
            or "Failed to stop execution."
        ),
        "tmux_stop": tmux_result,
        "strategy_stop": strategy_result or {"ok": False, "error": "No execution strategy configured."},
        "odin_stop": cli_result,
    }


def _apply_stop_transition(task, target_status, updated_by, reason, stop_result):
    """Persist post-stop status/metadata/history/comment updates.

    Note: _pre_stop_mark has already written ignore_execution_results,
    stopped_run_token, and the pending_stop_* keys. Here we consume the
    pending_* keys (they've been applied) while keeping the durable guards
    in place so any late execution_result webhook still gets discarded.
    """
    run_token = ((task.metadata or {}).get("active_execution") or {}).get("run_token")
    task_runs.finish_run(run_token, state=TaskRunState.KILLED)
    metadata = dict(task.metadata or {})
    metadata.pop("active_execution", None)
    metadata.pop("pending_stop_target", None)
    metadata.pop("pending_stop_updated_by", None)
    metadata.pop("pending_stop_reason", None)
    metadata["stop_generation"] = int(metadata.get("stop_generation", 0) or 0) + 1
    metadata["execution_stopped_at"] = timezone.now().isoformat()
    metadata["ignore_execution_results"] = True
    if run_token:
        metadata["stopped_run_token"] = run_token

    metadata["last_stop_request"] = {
        "actor": updated_by,
        "target_status": target_status,
        "reason": reason,
        "at": timezone.now().isoformat(),
    }

    task.kanban_position = move_task(task, target_status=target_status, target_index=None)
    task.status = target_status
    task.metadata = metadata
    task.save(update_fields=["status", "kanban_position", "metadata", "last_updated_at"])

    TaskHistory.objects.create(
        task=task,
        schedule_run=task.current_schedule_run,
        field_name="status",
        old_value=TaskStatus.EXECUTING,
        new_value=target_status,
        changed_by=updated_by,
    )

    TaskComment.objects.create(
        task=task,
        schedule_run=task.current_schedule_run,
        author_email=updated_by,
        author_label="taskit-ui",
        content=f"Execution stopped by user. Status changed to {target_status}.",
        comment_type=CommentType.STATUS_UPDATE,
    )
    maybe_finalize_schedule_run(task, target_status)

    return {"task": _task_response(task.id), "stop": stop_result}


def _reposition_best_effort(task, target_status):
    """Move a task in kanban order, but never let that kill a run result.

    The kanban reposition is bookkeeping relative to accepting an execution
    result: a stale column position is harmless, a dropped run result is not.
    ``move_task`` already retries transient locks; this is the decoupling
    backstop — if a lock still wins (retries exhausted, sustained writer
    contention), the reposition is skipped with a warning and the task keeps
    its existing position. The caller proceeds to persist the status change,
    history, and comment regardless. (docs/patterns/bookkeeping-never-kills-the-run.md)
    """
    try:
        return move_task(task, target_status=target_status, target_index=None)
    except OperationalError as exc:
        if not is_locked_error(exc):
            raise
        logger.warning(
            "Skipping kanban reposition for task %s (%s→%s): database locked "
            "after retries; keeping existing position. Run result is unaffected.",
            task.id, task.status, target_status, exc_info=True,
        )
        return int(task.kanban_position or 0)


@retry_on_locked(max_retries=3, base_delay=0.05)
def _persist_task_update(task, histories):
    """Persist a task PATCH (save + history bulk_create) atomically.

    Same backstop ``move_task`` has: the whole write is one atomic block
    retried on a transient ``database is locked``. Without this, a brief
    writer collision on PATCH /tasks/<id>/ surfaced as a 500 to operators
    and agents dispatching several tasks at once (task #364). ``save`` and
    ``bulk_create`` are one unit so the task row and its history never
    diverge — on a lock the transaction rolls back and both retry cleanly.
    """
    with transaction.atomic():
        task.save()
        if histories:
            TaskHistory.objects.bulk_create(histories)


@retry_on_locked(max_retries=3, base_delay=0.05)
def _create_task_comment(task, *, schedule_run=None, attachment_ids=None, **fields):
    """Create a task comment (and link any uploaded attachments) atomically,
    retried on a transient ``database is locked`` (task #364).

    The comment row and the attachment link are one unit: a lock on either
    rolls both back and the retry is clean (no orphan comment, no duplicate).
    """
    with transaction.atomic():
        comment = TaskComment.objects.create(
            task=task, schedule_run=schedule_run, **fields,
        )
        if attachment_ids:
            CommentAttachment.objects.filter(
                id__in=attachment_ids, task=task, comment__isnull=True,
            ).update(comment=comment)
        return comment


@retry_on_locked(max_retries=3, base_delay=0.05)
def _save_with_retry(instance, **save_kwargs):
    """Retry a single model save on a transient ``database is locked``.

    The generic backstop for operator-facing single-row writes (e.g. the
    IDE-settings endpoint) that don't need the atomic bulk_create pairing
    of ``_persist_task_update`` (task #364).
    """
    instance.save(**save_kwargs)


@retry_on_locked(max_retries=3, base_delay=0.05)
def _update_executor_max_concurrency(value):
    return SystemSetting.objects.update_or_create(
        key="executor_max_concurrency",
        defaults={"value": value},
    )


@retry_on_locked(max_retries=3, base_delay=0.05)
def _persist_label_change(task, *, m2m_change=None):
    """Apply an M2M label change (and any history row written inside the
    callable) in one atomic block, retried on a transient
    ``database is locked`` (task #364).

    The M2M write and the history write happen inside the same atomic
    block so a lock on either rolls both back and the retry is clean (no
    phantom history row, no half-applied label set).
    """
    with transaction.atomic():
        if m2m_change is not None:
            m2m_change(task)


class TaskViewSet(viewsets.ModelViewSet):
    serializer_class = TaskSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        query_params = self.request.query_params
        qs = (
            Task.objects.select_related("assignee", "spec")
            .prefetch_related("labels", "reflections")
            .annotate(comment_count=Count("comments"))
        )
        qs = _exclude_hidden_scheduled_tasks(qs, hide_terminal_recurring=self.action == "list")

        board_ids = _parse_multi_values(query_params, "board_id", aliases=("board",))
        if board_ids:
            qs = qs.filter(board_id__in=board_ids)

        spec_ids = _parse_multi_values(query_params, "spec_id", aliases=("spec",))
        if spec_ids:
            qs = qs.filter(spec_id__in=spec_ids)

        statuses = _parse_multi_values(query_params, "status")
        if statuses:
            qs = qs.filter(status__in=statuses)

        assignee_ids = _parse_multi_values(query_params, "assignee_id", aliases=("assignee",))
        if assignee_ids:
            qs = qs.filter(assignee_id__in=assignee_ids)

        priorities = _parse_multi_values(query_params, "priority")
        if priorities:
            qs = qs.filter(priority__in=priorities)

        label_ids = _parse_multi_values(query_params, "label_ids", aliases=("labels", "label"))
        if label_ids:
            qs = qs.filter(labels__id__in=label_ids).distinct()

        search = query_params.get("search") or query_params.get("q")
        if search:
            qs = qs.filter(Q(title__icontains=search) | Q(description__icontains=search))

        qs = _apply_date_range(qs, query_params, "created_at", "created_from", "created_to")
        qs = _apply_date_range(qs, query_params, "last_updated_at", "updated_from", "updated_to")

        tokens = _parse_sort_tokens(
            query_params.get("sort"),
            {"created_at", "title", "priority", "status"},
            default_tokens=[("created_at", True)],
        )
        qs = qs.order_by(*_build_order_by(tokens, {
            "created_at": "created_at",
            "title": "title",
            "priority": "priority",
            "status": "status",
        }))
        return qs

    def get_serializer_class(self):
        if self.action == "list":
            return TaskListSerializer
        return TaskSerializer

    def create(self, request):
        ser = CreateTaskSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        board = get_object_or_404(Board, pk=d["board_id"])
        normalized_depends_on = _validate_dependency_selection(board, d.get("depends_on", []))

        # Resolve created_by email from user ID if provided
        created_by = d.get("created_by", "")
        if d.get("created_by_user_id"):
            user = get_object_or_404(User, pk=d["created_by_user_id"])
            created_by = user.email

        # Resolve optional assignee
        assignee = None
        if d.get("assignee_id"):
            assignee = get_object_or_404(User, pk=d["assignee_id"])

        # Resolve optional spec
        spec = None
        if d.get("spec_id"):
            spec = get_object_or_404(Spec, pk=d["spec_id"])

        # Resolve model_name: explicit field, fallback to metadata
        model_name = d.get("model_name")
        if not model_name:
            metadata = d.get("metadata", {})
            if isinstance(metadata, dict):
                model_name = metadata.get("selected_model") or metadata.get("model")
        if not model_name and assignee:
            model_name = _default_model_for_user(assignee)
        _validate_forced_task_target(assignee=assignee, model_name=model_name)
        _validate_dispatch_readiness(
            board=board,
            spec_id=d.get("spec_id"),
            is_dispatch_transition=d.get("status", "TODO") == TaskStatus.IN_PROGRESS,
        )

        task = Task.objects.create(
            board=board,
            title=d["title"],
            description=d.get("description", ""),
            priority=d.get("priority", "MEDIUM"),
            status=d.get("status", "TODO"),
            created_by=created_by,
            assignee=assignee,
            spec=spec,
            dev_eta_seconds=d.get("dev_eta_seconds"),
            depends_on=normalized_depends_on,
            complexity=d.get("complexity"),
            metadata=d.get("metadata", {}),
            model_name=model_name,
            skip_reflection=d.get("skip_reflection", False),
        )
        task.kanban_position = move_task(task, target_status=task.status, target_index=0)

        # Auto-add assignee to board if not already a member
        _ensure_board_membership(board, assignee)

        # Auto-add model to assignee's available_models
        if assignee and model_name:
            _ensure_model_on_user(assignee, model_name)

        # Set labels if provided
        label_ids = d.get("label_ids", [])
        if label_ids:
            labels = Label.objects.filter(id__in=label_ids)
            task.labels.set(labels)

        TaskHistory.objects.create(
            task=task,
            field_name="created",
            old_value="",
            new_value="Task created",
            changed_by=created_by,
        )

        logger.info(
            "Task created: id=%s, title=%s, spec=%s, agent=%s, model=%s, status=%s",
            task.id, task.title, task.spec_id, assignee.name if assignee else None,
            model_name, task.status,
        )

        return Response(_task_response(task.id), status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def rework(self, request, pk=None):
        """Compose a follow-up task from this task + a one-sentence instruction.

        No model call — pure assembly (tasks/rework.py). The new task is a
        normal TODO task on the same board (+ spec, if any); existing
        machinery (twins/quote at dispatch, agent routing) picks it up the
        same way it would any other task. See tasks/rework.py for the
        guard rails on the instruction.
        """
        parent = get_object_or_404(Task, pk=pk)
        ser = ReworkTaskSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        created_by = d.get("created_by", "")
        if d.get("created_by_user_id"):
            user = get_object_or_404(User, pk=d["created_by_user_id"])
            created_by = user.email

        try:
            new_task = compose_rework_task(parent, d["instruction"], created_by=created_by)
        except ReworkValidationError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        logger.info(
            "Task reworked: parent_id=%s, new_id=%s, created_by=%s",
            parent.id, new_task.id, created_by,
        )

        return Response(_task_response(new_task.id), status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        task = self.get_object()
        logger.debug("Task update payload: task_id=%s, data=%s", task.id, request.data)
        ser = UpdateTaskSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        if (
            task.schedule_id
            and task.schedule
            and task.schedule.status in [ScheduleStatus.ACTIVE, ScheduleStatus.PAUSED]
            and task.schedule.next_run_at_utc
            and task.schedule.next_run_at_utc > timezone.now()
            and d.get("status") == TaskStatus.IN_PROGRESS
            and task.status not in [TaskStatus.IN_PROGRESS, TaskStatus.EXECUTING]
        ):
            return Response(
                {"detail": "This task is controlled by a schedule and cannot be started before its due time."},
                status=status.HTTP_409_CONFLICT,
            )
        lock_response = _check_executing_mutation_lock(task, d)
        if lock_response is not None:
            return lock_response
        if "kanban_target_status" in d:
            if "status" in d and d["kanban_target_status"] != d["status"]:
                raise ValidationError({"kanban_target_status": "Must match status when both are provided."})
            if "status" not in d and d["kanban_target_status"] != task.status:
                raise ValidationError({"kanban_target_status": "Cannot differ from current status without status update."})

        # CANCELED transition gate (fable task 192). The transition matrix
        # in tasks.state_transitions forbids DONE → CANCELED and direct
        # EXECUTING → CANCELED via PATCH; only the stop_execution endpoint
        # can land an EXECUTING task in CANCELED. EXECUTING is also caught
        # earlier by _check_executing_mutation_lock (→ 409), but an explicit
        # 400 here keeps the contract tight for callers that bypass the
        # lock (e.g. tests, scripts).
        if "status" in d and d["status"] == TaskStatus.CANCELED and d["status"] != task.status:
            if not is_cancel_transition_allowed(task.status, d["status"]):
                if str(task.status) == TaskStatus.DONE:
                    detail = (
                        f"Task {task.id} is DONE (terminal). CANCELED is not "
                        "allowed from a finalized task — DONE cannot be "
                        "retroactively canceled."
                    )
                elif str(task.status) == TaskStatus.EXECUTING:
                    detail = (
                        f"Task {task.id} is EXECUTING. To CANCELED, use "
                        "/tasks/{id}/stop_execution/ with target_status=CANCELED "
                        "so the running process is also torn down."
                    )
                else:
                    detail = (
                        f"Task {task.id} is {task.status}; CANCELED is not a "
                        "valid transition from this status."
                    )
                return Response(
                    {"detail": detail, "code": "invalid_cancel_transition"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        updated_by = d["updated_by"]
        histories = []
        normalized_depends_on = None

        old_status = task.status
        target_status = d.get("kanban_target_status") or d.get("status") or old_status
        target_index = d.get("kanban_target_index")

        # Simple scalar fields — same pattern: compare str(old) vs str(new)
        for field in ("title", "description", "dev_eta_seconds", "priority",
                       "status", "complexity", "model_name"):
            if field in d:
                old_val = getattr(task, field)
                if _record_change(histories, task, field, old_val, d[field], updated_by):
                    setattr(task, field, d[field])

        # Assignee FK — format as str(id) or "" for history
        if "assignee_id" in d:
            old_assignee = str(task.assignee_id) if task.assignee_id else ""
            new_assignee = str(d["assignee_id"]) if d["assignee_id"] else ""
            if _record_change(histories, task, "assignee_id", old_assignee, new_assignee, updated_by):
                task.assignee_id = d["assignee_id"]
                # Auto-add new assignee to board
                if d["assignee_id"]:
                    new_user = User.objects.get(pk=d["assignee_id"])
                    _ensure_board_membership(task.board, new_user)
                    # Default First (F45): an assignee without an explicit model
                    # gets the agent's default model — a missing model_name must
                    # never reach dispatch and fail the run.
                    if not d.get("model_name") and not task.model_name:
                        default_model = _default_model_for_user(new_user)
                        if default_model and _record_change(
                            histories, task, "model_name", task.model_name or "", default_model, updated_by,
                        ):
                            task.model_name = default_model

        # Labels M2M — compare as sorted JSON lists
        if "label_ids" in d:
            old_labels = json.dumps(sorted(task.labels.values_list("id", flat=True)))
            new_labels = json.dumps(sorted(d["label_ids"]))
            if _record_change(histories, task, "labels", old_labels, new_labels, updated_by):
                task.labels.set(Label.objects.filter(id__in=d["label_ids"]))

        # JSON fields — compare via json.dumps for stable comparison
        if "depends_on" in d:
            normalized_depends_on = _validate_dependency_selection(task.board, d["depends_on"], task=task)
            old_json = json.dumps(_normalize_dependency_ids(task.depends_on))
            new_json = json.dumps(normalized_depends_on)
            if _record_change(histories, task, "depends_on", old_json, new_json, updated_by):
                task.depends_on = normalized_depends_on

        if "metadata" in d:
            old_json = json.dumps(task.metadata, sort_keys=True)
            new_json = json.dumps(d["metadata"], sort_keys=True)
            if _record_change(histories, task, "metadata", old_json, new_json, updated_by):
                task.metadata = d["metadata"]

        # spec_id write — resolves the FK and is the only way to associate a
        # task with a spec after creation. Previously UpdateTaskSerializer
        # had no spec_id field, so PATCH /tasks/:id/ with a spec could not
        # actually wire the task up to a spec. (Task #114 evidence.)
        if "spec_id" in d:
            new_spec_id = d["spec_id"]
            old_spec_id = str(task.spec_id) if task.spec_id else ""
            new_spec_id_str = str(new_spec_id) if new_spec_id else ""
            if old_spec_id != new_spec_id_str:
                spec = None
                if new_spec_id is not None:
                    spec = get_object_or_404(Spec, pk=new_spec_id)
                histories.append(TaskHistory(
                    task=task, schedule_run=task.current_schedule_run,
                    field_name="spec_id",
                    old_value=old_spec_id, new_value=new_spec_id_str,
                    changed_by=updated_by,
                ))
                task.spec = spec

        _validate_dispatch_readiness(
            board=task.board,
            spec_id=task.spec_id,
            is_dispatch_transition=(
                target_status == TaskStatus.IN_PROGRESS
                and old_status not in (TaskStatus.IN_PROGRESS, TaskStatus.EXECUTING)
            ),
        )

        if target_index is not None or old_status != target_status:
            current_status = task.status
            task.status = old_status
            task.kanban_position = move_task(task, target_status=target_status, target_index=target_index)
            task.status = current_status

        _validate_forced_task_target(assignee=task.assignee, model_name=task.model_name)

        # Keep metadata["selected_model"] in sync with model_name so odin
        # picks up UI-driven model changes at execution time.
        if "model_name" in d and d["model_name"]:
            task.metadata = dict(task.metadata or {})
            task.metadata["selected_model"] = d["model_name"]

        # W6.14: human override of routing. When a human changes the
        # assignee or the model after dispatch, the WHY line in the UI
        # must flip from "Auto" to "Override" and record who did it.
        # We only stamp the flag when the picked agent/model actually
        # moved — edits that don't touch routing leave the dispatch-time
        # dict intact. Machine actors (odin sync, agent harness, system
        # workers) never count as overrides — odin PATCHes assignee/model
        # right after planning to sync its own routing decision, and that
        # must keep rendering as "Auto".
        assignee_history = [h for h in histories if h.field_name == "assignee_id"]
        model_history = [h for h in histories if h.field_name == "model_name"]
        routing_changed = bool(assignee_history or model_history)
        if routing_changed and is_human_author(updated_by):
            task.metadata = dict(task.metadata or {})
            existing = dict(task.metadata.get("assignment_reason") or {})
            existing["override"] = True
            existing["override_by"] = updated_by or ""
            # Mirror the new pick so the UI can render without an extra
            # fetch. Fall back to current assignee.name if the
            # FK lookup happened above (no need to re-query).
            new_user = task.assignee
            if new_user is not None:
                existing["agent"] = new_user.name
            if task.model_name:
                existing["model"] = task.model_name
            task.metadata["assignment_reason"] = existing

        if "skip_reflection" in d:
            if _record_change(histories, task, "skip_reflection", task.skip_reflection, d["skip_reflection"], updated_by):
                task.skip_reflection = d["skip_reflection"]

        # A new queue/run cycle clears stale stop guards from prior executions.
        if "status" in d and d["status"] == TaskStatus.IN_PROGRESS and old_status != TaskStatus.IN_PROGRESS:
            task.metadata = dict(task.metadata or {})
            _clear_stop_guards(task.metadata)

        _persist_task_update(task, histories)

        if histories:
            for h in histories:
                if h.field_name == "status":
                    logger.important(
                        "Task %s status: %s \u2192 %s (by %s)",
                        task.id, h.old_value, h.new_value, updated_by,
                    )

        # Trigger execution strategy if status changed to IN_PROGRESS
        if "status" in d and d["status"] == "IN_PROGRESS" and old_status != "IN_PROGRESS":
            logger.important(
                "Task %s status changed: %s -> IN_PROGRESS (assignee=%s)",
                task.id, old_status, task.assignee_id,
            )
            if task.assignee_id:
                from .execution import get_strategy
                strategy = get_strategy()
                if strategy:
                    logger.info("Firing execution strategy for task %s", task.id)
                    strategy.trigger(task)
                else:
                    logger.warning(
                        "No execution strategy configured — task %s moved to IN_PROGRESS but won't be executed. "
                        "Set ODIN_EXECUTION_STRATEGY=local in .env to enable.",
                        task.id,
                    )
            else:
                logger.warning(
                    "Task %s moved to IN_PROGRESS but has no assignee — skipping execution trigger",
                    task.id,
                )

        # Trigger auto-reflection when status changes to REVIEW
        if "status" in d and d["status"] == TaskStatus.REVIEW and old_status != TaskStatus.REVIEW:
            _trigger_auto_reflection(task)

        # API-driven DONE transition: the operator's real flow.  The
        # spec-finalization path also calls cleanup, but operators
        # promote via PATCH not via `odin spec finalize`, so without
        # this hook API promotions leak worktrees indefinitely.
        # Cleanup failures never block the transition (matches the
        # spec-finalization contract).
        if (
            "status" in d
            and d["status"] == TaskStatus.DONE
            and old_status != TaskStatus.DONE
        ):
            try:
                from .dag_executor import _cleanup_done_task_worktree
                _cleanup_done_task_worktree(task)
            except Exception:
                logger.warning(
                    "Task %s: worktree cleanup on API-driven DONE failed",
                    task.id, exc_info=True,
                )
            # Memory: stamp estimate vs actual on operator-driven DONE flip.
            # Same trail as the auto-promote + spec-finalize paths; best-effort.
            try:
                from .estimation import stamp_actual_and_trail
                stamp_actual_and_trail(task, transition="api_patch_done")
            except Exception:
                logger.warning(
                    "Task %s: estimate-vs-actual stamp failed on PATCH-DONE",
                    task.id, exc_info=True,
                )

        if "status" in d and d["status"] != old_status:
            maybe_finalize_schedule_run(task, d["status"])

        return Response(_task_response(task.id))

    @action(detail=True, methods=["post"], url_path="stop_execution")
    def stop_execution(self, request, pk=None):
        """Stop an actively executing task, then move it to a target status."""
        task = get_object_or_404(Task, pk=pk)
        ser = StopExecutionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        if task.status != TaskStatus.EXECUTING:
            return Response(
                {"detail": f"Task {task.id} is {task.status}, expected EXECUTING."},
                status=status.HTTP_409_CONFLICT,
            )

        body, http_status = _perform_stop_flow(
            task=task,
            target_status=d["target_status"],
            updated_by=d["updated_by"],
            reason=(d.get("reason") or "").strip() or "user_drag_stop_confirm",
        )
        return Response(body, status=http_status)

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        task = get_object_or_404(Task, pk=pk)
        if task.status == TaskStatus.EXECUTING:
            return _executing_lock_response(task, ["assignee"])
        ser = AssignTaskSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        assignee = get_object_or_404(User, pk=ser.validated_data["assignee_id"])

        # W10.4: refuse to assign a task to an AGENT that is not currently
        # enabled on this board. Without this guard, an operator could
        # dispatch work to a roster-disabled agent and the silent fallback
        # ("pick the cheap tier") would mask the real cause — the very
        # symptom the task is here to fix. Humans/operators are not gated
        # here because they may legitimately act across boards while
        # editing projects; only agent routing respects the roster.
        if (
            task.board_id
            and (assignee.role or "").upper() == "AGENT"
            and not BoardMembership.objects.filter(
                board_id=task.board_id, user=assignee,
            ).exists()
        ):
            from rest_framework.exceptions import ValidationError as _VErr
            settings_path = _agent_settings_hint(task.board_id, assignee.name)
            raise _VErr({
                "assignee_id": (
                    f"Agent '{assignee.name}' is not enabled on this board. "
                    f"Enable it at {settings_path} or pick a different agent."
                ),
                "settings_path": settings_path,
                "board_id": task.board_id,
                "agent": assignee.name,
            })
        # Default First (F45): assigning an agent to a model-less task resolves
        # the agent's default model, so dispatch never runs with a stale or
        # missing model.
        default_model = None
        if not task.model_name:
            default_model = _default_model_for_user(assignee)
        _validate_forced_task_target(
            assignee=assignee, model_name=task.model_name or default_model,
        )
        old_assignee = str(task.assignee_id) if task.assignee_id else ""
        histories = []
        if old_assignee != str(assignee.id):
            histories.append(TaskHistory(
                task=task, schedule_run=task.current_schedule_run,
                field_name="assignee_id",
                old_value=old_assignee, new_value=str(assignee.id),
                changed_by=ser.validated_data["updated_by"],
            ))

        task.assignee = assignee
        if default_model:
            histories.append(TaskHistory(
                task=task, schedule_run=task.current_schedule_run,
                field_name="model_name",
                old_value="", new_value=default_model,
                changed_by=ser.validated_data["updated_by"],
            ))
            task.model_name = default_model
        _persist_task_update(task, histories)

        # Auto-add assignee to board
        _ensure_board_membership(task.board, assignee)

        # Notify assignee
        from .notification_service import notify
        notify(
            recipient_ids=[assignee.id],
            notification_type="task_assigned",
            title=f'You were assigned to "{task.title}"',
            task=task,
            board=task.board,
            actor_email=ser.validated_data["updated_by"],
        )

        return Response(_task_response(task.id))

    @action(detail=True, methods=["post"])
    def unassign(self, request, pk=None):
        task = get_object_or_404(Task, pk=pk)
        if task.status == TaskStatus.EXECUTING:
            return _executing_lock_response(task, ["assignee"])
        ser = UnassignTaskSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        old_assignee = str(task.assignee_id) if task.assignee_id else ""
        histories = []
        if old_assignee:
            histories.append(TaskHistory(
                task=task, schedule_run=task.current_schedule_run,
                field_name="assignee_id",
                old_value=old_assignee, new_value="",
                changed_by=ser.validated_data["updated_by"],
            ))

        task.assignee = None
        _persist_task_update(task, histories)

        return Response(_task_response(task.id))

    @action(detail=True, methods=["post"], url_path="labels", url_name="add-labels")
    def add_labels(self, request, pk=None):
        task = get_object_or_404(Task, pk=pk)
        ser = AddLabelsSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        old_labels = json.dumps(sorted(task.labels.values_list("id", flat=True)))
        labels = Label.objects.filter(id__in=ser.validated_data["label_ids"])

        def _record_and_apply(t):
            t.labels.add(*labels)
            new = json.dumps(sorted(t.labels.values_list("id", flat=True)))
            if old_labels != new:
                TaskHistory.objects.create(
                    task=t, schedule_run=t.current_schedule_run,
                    field_name="labels",
                    old_value=old_labels, new_value=new,
                    changed_by=ser.validated_data["updated_by"],
                )
        _persist_label_change(task, m2m_change=_record_and_apply)
        return Response(_task_response(task.id))

    @action(detail=True, methods=["delete"], url_path="labels", url_name="remove-labels")
    def remove_labels(self, request, pk=None):
        task = get_object_or_404(Task, pk=pk)
        ser = AddLabelsSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        old_labels = json.dumps(sorted(task.labels.values_list("id", flat=True)))
        labels = Label.objects.filter(id__in=ser.validated_data["label_ids"])

        def _record_and_apply(t):
            t.labels.remove(*labels)
            new = json.dumps(sorted(t.labels.values_list("id", flat=True)))
            if old_labels != new:
                TaskHistory.objects.create(
                    task=t, schedule_run=t.current_schedule_run,
                    field_name="labels",
                    old_value=old_labels, new_value=new,
                    changed_by=ser.validated_data["updated_by"],
                )
        _persist_label_change(task, m2m_change=_record_and_apply)
        return Response(_task_response(task.id))

    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        get_object_or_404(Task, pk=pk)
        histories = TaskHistory.objects.filter(task_id=pk)
        paginator = HistoryPagination()
        page = paginator.paginate_queryset(histories, request)
        if page is not None:
            return paginator.get_paginated_response(TaskHistorySerializer(page, many=True).data)
        return Response(TaskHistorySerializer(histories, many=True).data)

    @action(detail=True, methods=["get"], url_path="session")
    def session(self, request, pk=None):
        """Return the current (or most recent) session metadata for a task.

        A session is one run of either task execution or reflection. The
        response shape mirrors SessionInfo and is consumed by the frontend to
        render the 'Open current session' button label and by the session
        WebSocket consumer to know which JSONL file to tail.
        """
        from pathlib import Path
        from .session_resolver import resolve_session, _log_dir_for_task
        from .execution.utils import resolve_working_dir

        task = get_object_or_404(
            Task.objects.select_related("board", "spec"), pk=pk,
        )
        info = resolve_session(task)

        # Diagnostic fields — helps debug path mismatches between odin and resolver.
        working_dir = resolve_working_dir(task)
        log_dir = _log_dir_for_task(task)
        diag = {
            "working_dir": working_dir,
            "log_dir": str(log_dir) if log_dir else None,
            "log_dir_exists": log_dir.is_dir() if log_dir else False,
            "trace_files": sorted(str(p.name) for p in log_dir.glob("*.trace.jsonl")) if log_dir and log_dir.is_dir() else [],
        }

        if info is None:
            return Response({
                "available": False,
                "task_id": task.id,
                "diagnostic": diag,
            })
        payload = info.to_dict()
        payload["available"] = True
        payload["diagnostic"] = diag
        return Response(payload)

    @action(detail=True, methods=["get", "post"])
    def comments(self, request, pk=None):
        task = get_object_or_404(Task, pk=pk)
        if request.method == "GET":
            comments = TaskComment.objects.filter(task=task)
            after_id = request.query_params.get("after")
            if after_id:
                comments = comments.filter(id__gt=int(after_id))
            type_filter = request.query_params.get("type")
            if type_filter:
                comments = comments.filter(comment_type=type_filter)
            paginator = CommentPagination()
            page = paginator.paginate_queryset(comments, request)
            ctx = {"request": request}
            if page is not None:
                return paginator.get_paginated_response(TaskCommentSerializer(page, many=True, context=ctx).data)
            return Response(TaskCommentSerializer(comments, many=True, context=ctx).data)
        # POST
        ser = CreateTaskCommentSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        attachment_ids = data.pop("attachment_ids", [])
        comment = _create_task_comment(
            task,
            schedule_run=task.current_schedule_run,
            attachment_ids=attachment_ids,
            **data,
        )
        logger.info(
            "Comment on task %s by %s: %s",
            task.id, comment.author_label or comment.author_email, comment.content[:100],
        )

        # Notify board members about new comment — skip if content is JSON (agent/system messages)
        try:
            json.loads(comment.content)
            _is_json = True
        except (json.JSONDecodeError, ValueError, TypeError):
            _is_json = False

        if not _is_json:
            from .notification_service import notify
            member_ids = list(
                BoardMembership.objects.filter(board=task.board).values_list("user_id", flat=True)
            )
            notify(
                recipient_ids=member_ids,
                notification_type="comment_added",
                title=f'New comment on "{task.title}"',
                body=comment.content[:200],
                task=task,
                board=task.board,
                actor_email=comment.author_email,
            )

        return Response(
            TaskCommentSerializer(comment, context={"request": request}).data, status=status.HTTP_201_CREATED
        )

    MAX_SCREENSHOT_SIZE = 10 * 1024 * 1024  # 10 MB

    @action(detail=True, methods=["post"], url_path="screenshots")
    def screenshots(self, request, pk=None):
        """Upload screenshot files as proof evidence for a task."""
        task = get_object_or_404(Task, pk=pk)
        files = request.FILES.getlist("files")
        if not files:
            return Response(
                {"detail": "No files provided. Include one or more 'files' in the upload."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Validate file sizes
        for f in files:
            if f.size > self.MAX_SCREENSHOT_SIZE:
                return Response(
                    {"detail": f"File '{f.name}' exceeds 10 MB limit."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        author_email = request.data.get("author_email", "agent@odin.agent")
        created = []
        for f in files:
            attachment = CommentAttachment.objects.create(
                task=task,
                file=f,
                original_filename=f.name,
                content_type=f.content_type or "application/octet-stream",
                file_size=f.size,
                uploaded_by=author_email,
            )
            created.append(attachment)

        serializer = CommentAttachmentSerializer(
            created, many=True, context={"request": request}
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def question(self, request, pk=None):
        """Post a question comment — sets has_pending_question metadata flag."""
        task = get_object_or_404(Task, pk=pk)
        ser = CreateTaskCommentSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        comment = TaskComment.objects.create(
            task=task,
            schedule_run=task.current_schedule_run,
            author_email=ser.validated_data["author_email"],
            author_label=ser.validated_data.get("author_label", ""),
            content=ser.validated_data["content"],
            attachments=[{"type": "question", "status": "pending"}],
            comment_type=CommentType.QUESTION,
        )

        task.metadata = task.metadata or {}
        task.metadata["has_pending_question"] = True
        # Freeze the EXECUTING timer while waiting for the answer
        if task.status == TaskStatus.EXECUTING and not task.metadata.get("question_paused_at"):
            task.metadata["question_paused_at"] = timezone.now().isoformat()
        task.save(update_fields=["metadata"])

        # Notify board human members about the question
        from .notification_service import notify
        member_ids = list(
            BoardMembership.objects.filter(board=task.board).values_list("user_id", flat=True)
        )
        notify(
            recipient_ids=member_ids,
            notification_type="question_asked",
            title=f'Question on "{task.title}"',
            body=ser.validated_data["content"][:200],
            task=task,
            board=task.board,
            actor_email=ser.validated_data["author_email"],
        )

        return Response(
            TaskCommentSerializer(comment, context={"request": request}).data, status=status.HTTP_201_CREATED
        )

    @action(
        detail=True,
        methods=["post"],
        url_path=r"comments/(?P<comment_id>\d+)/reply",
        url_name="comment-reply",
    )
    def reply(self, request, pk=None, comment_id=None):
        """Reply to a question comment — clears has_pending_question metadata flag."""
        task = get_object_or_404(Task, pk=pk)
        question_comment = get_object_or_404(
            TaskComment, pk=comment_id, task=task
        )

        ser = CreateTaskCommentSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        reply_comment = TaskComment.objects.create(
            task=task,
            schedule_run=task.current_schedule_run,
            author_email=ser.validated_data["author_email"],
            author_label=ser.validated_data.get("author_label", ""),
            content=ser.validated_data["content"],
            attachments=[{"type": "reply", "reply_to": int(comment_id)}],
            comment_type=CommentType.REPLY,
        )

        # Mark original question as answered
        attachments = list(question_comment.attachments)
        for att in attachments:
            if isinstance(att, dict) and att.get("type") == "question":
                att["status"] = "answered"
        question_comment.attachments = attachments
        question_comment.save(update_fields=["attachments"])

        # Clear pending question flag and resume EXECUTING timer
        task.metadata = task.metadata or {}
        task.metadata.pop("has_pending_question", None)
        paused_at = task.metadata.pop("question_paused_at", None)
        if paused_at:
            paused_start = datetime.fromisoformat(paused_at)
            pause_ms = (timezone.now() - paused_start).total_seconds() * 1000
            task.metadata["executing_paused_ms"] = (
                task.metadata.get("executing_paused_ms", 0) + pause_ms
            )
        task.save(update_fields=["metadata"])

        return Response(
            TaskCommentSerializer(reply_comment, context={"request": request}).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"], url_path="execution_result")
    def execution_result(self, request, pk=None):
        """Record a complete execution result — single atomic operation.

        Receives the raw agent output, extracts text, parses the ODIN-STATUS
        envelope, composes a metrics-inline comment, records status change in
        history, and creates the comment. Replaces the old dual-call pattern
        (PUT status + POST comment).
        """
        from .execution_processing import extract_agent_text, parse_envelope, compose_comment

        task = get_object_or_404(Task, pk=pk)
        ser = ExecutionResultSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        exec_result = d["execution_result"]
        new_status = d["status"]
        updated_by = d["updated_by"]
        exec_meta = exec_result.get("metadata", {}) or {}

        incoming_run_token = (exec_meta.get("taskit_run_token") or "").strip()

        # TaskRun write fencing (task #210): a write whose run_token doesn't
        # match the task's current RUNNING run is from a superseded/zombie
        # run — reject it outright instead of silently corrupting the live
        # run's status. Tasks not (yet) tracked by TaskRun — legacy dispatch
        # paths, direct API calls in tests — have no current run, so this
        # gate is skipped for them; the stopped_run_token guard below still
        # covers the explicit-stop case for those.
        current_run = task_runs.current_running_run(task)
        if current_run is not None and incoming_run_token and incoming_run_token != current_run.run_token:
            logger.warning(
                "Rejecting execution_result for task %s: run_token=%s does not match "
                "current RUNNING run_token=%s (stale/zombie write)",
                task.id, incoming_run_token, current_run.run_token,
            )
            return Response(
                {
                    "detail": (
                        f"Task {task.id}'s run has been superseded by a newer "
                        "dispatch; this write is from a stale run and was rejected."
                    ),
                    "code": "stale_run_token",
                },
                status=status.HTTP_409_CONFLICT,
            )

        stopped_run_token = (task.metadata or {}).get("stopped_run_token")
        if (task.metadata or {}).get("ignore_execution_results"):
            if not stopped_run_token or not incoming_run_token or incoming_run_token == stopped_run_token:
                logger.info(
                    "Ignoring stale execution_result for task %s (status=%s, incoming_token=%s)",
                    task.id, task.status, incoming_run_token or "-",
                )
                return Response(_task_response(task.id))

        # 1. Extract clean text from structured CLI output
        agent_text, extracted_usage = extract_agent_text(exec_result["raw_output"])

        # 2. Parse ODIN-STATUS envelope
        clean_output, parsed_success, summary = parse_envelope(agent_text)

        # Override success from envelope if available
        success = exec_result["success"]
        if parsed_success is not None:
            success = parsed_success

        # 3. Compose comment
        verb = "Completed" if success else "Failed"
        failure_type = (exec_result.get("failure_type") or "").strip()
        failure_reason = (exec_result.get("failure_reason") or "").strip()
        failure_origin = (exec_result.get("failure_origin") or "").strip()
        failure_debug = (exec_result.get("metadata", {}) or {}).get("failure_debug")

        if success:
            summary_text = summary or "Completed successfully"
        else:
            base_reason = failure_reason or exec_result.get("error") or "unknown error"
            lines = [
                summary or f"Failed: {base_reason}",
            ]
            if failure_type:
                lines.append(f"Failure type: {failure_type}")
            lines.append(f"Reason: {base_reason}")
            if failure_origin:
                lines.append(f"Origin: {failure_origin}")
            if failure_debug:
                lines.append(f"Debug: {str(failure_debug)[:FAILURE_DEBUG_PREVIEW_LIMIT]}")
            summary_text = "\n".join(lines)

        comment_text = compose_comment(
            verb,
            exec_result.get("duration_ms"),
            exec_result.get("metadata", {}),
            summary_text,
        )

        # 4. Record status change in history
        histories = []
        old_status = task.status
        if old_status != new_status:
            task.kanban_position = _reposition_best_effort(task, new_status)
            _record_change(histories, task, "status", old_status, new_status, updated_by)
            task.status = new_status

        # 5. Store execution metadata on task
        task_metadata = task.metadata or {}
        if exec_result.get("duration_ms"):
            task_metadata["last_duration_ms"] = exec_result["duration_ms"]
        # Usage is now computed on-the-fly from trace comments (see compute_usage_from_trace).
        # No longer cached in metadata — the trace comment is the source of truth.
        if exec_meta.get("selected_model"):
            task_metadata["selected_model"] = exec_meta["selected_model"]
        # Task #331: an unconfirmed completion reaches REVIEW with a flag —
        # the agent never emitted an ODIN-STATUS verdict but the worktree
        # had real changes, so the reviewer must judge the diff, not the
        # missing marker. Persist the flag so the reviewer sees it
        # programmatically (a visible comment is posted by odin too).
        if success and exec_meta.get("unconfirmed_completion"):
            task_metadata["unconfirmed_completion"] = exec_meta["unconfirmed_completion"]
        else:
            task_metadata.pop("unconfirmed_completion", None)
        # Task #332: resume-on-truncation bookkeeping. A resumable truncation
        # (output cap hit mid-task with work in the worktree) leaves the task
        # FAILED so the truncation AUTO_REQUEUE policy requeues it into the
        # same worktree with a host-built resume prompt. ``truncation_resume_
        # pending`` tells the NEXT dispatch to rebuild that prompt; the count
        # is a durable capability signal task #328's routing table reads. The
        # count is monotonic (kept even after a successful resume); pending is
        # cleared the moment a run is NOT a resumable truncation.
        if exec_meta.get("truncation_resume_count") is not None:
            task_metadata["truncation_resume_count"] = exec_meta["truncation_resume_count"]
        if not success and exec_meta.get("truncation_resume_pending"):
            task_metadata["truncation_resume_pending"] = True
        else:
            task_metadata.pop("truncation_resume_pending", None)
        if not success:
            if failure_type:
                task_metadata["last_failure_type"] = failure_type
            if failure_reason:
                task_metadata["last_failure_reason"] = failure_reason[:FAILURE_REASON_LIMIT]
            elif exec_result.get("error"):
                task_metadata["last_failure_reason"] = str(exec_result.get("error"))[:FAILURE_REASON_LIMIT]
            if failure_origin:
                task_metadata["last_failure_origin"] = failure_origin[:FAILURE_ORIGIN_LIMIT]
            tag_failure_class(task_metadata)
        # Accumulate estimated cost across retries (sum, not overwrite)
        if exec_meta.get("estimated_cost_usd") is not None:
            existing_cost = task_metadata.get("total_estimated_cost_usd") or 0.0
            task_metadata["total_estimated_cost_usd"] = existing_cost + exec_meta["estimated_cost_usd"]
        # Store effective input and full output for debugging
        if exec_result.get("effective_input"):
            task_metadata["effective_input"] = exec_result["effective_input"][:EFFECTIVE_INPUT_LIMIT]
        task_metadata["full_output"] = agent_text
        if old_status == TaskStatus.EXECUTING and new_status != TaskStatus.EXECUTING:
            task_metadata.pop("active_execution", None)
            _clear_stop_guards(task_metadata)
            task_runs.finish_run(
                current_run.run_token if current_run else incoming_run_token,
                state=TaskRunState.FINISHED,
            )
        task.metadata = task_metadata

        _persist_task_update(task, histories)

        # Mistakes ledger (task #223): a terminal FAILED (not requeued away)
        # is distilled to one line. Idempotent per run_token.
        if task.status == TaskStatus.FAILED:
            record_execution_mistake(task, run_token=incoming_run_token)

        # 6. Create comment (suppressed when byte-identical to the last one —
        # a retried execution_result POST must not duplicate the verdict line
        # (task #360); creation retries on a locked database (task #364)).
        from .comment_dedup import has_identical_comment
        if not has_identical_comment(task, comment_text, author_email=updated_by):
            _create_task_comment(
                task,
                author_email=updated_by,
                author_label=exec_result.get("agent", ""),
                content=comment_text,
                comment_type=CommentType.STATUS_UPDATE,
            )

        spec_ctx = f", spec={task.spec_id}" if task.spec_id else ""
        logger.important(
            "Execution result for task %s%s: success=%s, agent=%s, duration=%sms, model=%s, status=%s\u2192%s",
            task.id, spec_ctx, success, exec_result.get("agent"),
            exec_result.get("duration_ms"), exec_meta.get("selected_model"),
            old_status, new_status,
        )

        # Unified routing policy on execution failure (task #328). Odin
        # reports FAILED to THIS endpoint; the Celery worker path uses the
        # same dispatcher (dag_executor). Both now route through ONE table
        # (tasks.failure_policy) instead of the old ad-hoc tier-jump
        # (_maybe_escalate_model), which escalated a protocol hiccup onto
        # expensive firepower. Per class: protocol/infra \u2192 retry same agent,
        # then same-tier routing peer, never a tier jump; crash / env / disk
        # / unknown \u2192 hold for human. The dispatcher flips FAILED \u2192
        # IN_PROGRESS and re-triggers execution itself when it requeues.
        if not success and task.status == TaskStatus.FAILED:
            task.refresh_from_db()
            from .failure_policy import apply_failure_policy
            if not apply_failure_policy(task):
                # No policy match (human action or failure_class missing):
                # legacy type-based infra fallback preserves auto-retry.
                from .dag_executor import _maybe_auto_redispatch_infra_failure
                _maybe_auto_redispatch_infra_failure(task)
            new_status = task.status

        # Trigger auto-reflection when execution result moves task to REVIEW
        if new_status == TaskStatus.REVIEW and old_status != TaskStatus.REVIEW:
            _trigger_auto_reflection(task)

        return Response(_task_response(task.id))

    @action(detail=True, methods=["post"], url_path="summarize")
    def summarize(self, request, pk=None):
        """Dispatch an async summarize via the Odin execution pipeline.

        Sets task.metadata["summarize_in_progress"] = True, dispatches
        ``odin summarize <task_id>`` via the configured execution strategy
        (or as a direct subprocess fallback), and returns 202 Accepted.
        """
        task = get_object_or_404(Task, pk=pk)

        # Set in-progress flag
        task.metadata = task.metadata or {}
        task.metadata["summarize_in_progress"] = True
        task.save(update_fields=["metadata"])

        # Dispatch via execution strategy (fall back to shared subprocess spawner)
        from .execution import get_strategy
        from .execution.base import spawn_summarize_subprocess
        strategy = get_strategy()
        if strategy:
            strategy.trigger_summarize(task)
        else:
            spawn_summarize_subprocess(task)

        logger.info("Summarize dispatched for task %s", task.id)
        return Response({"status": "summarizing"}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["get"], url_path="detail")
    def detail_view(self, request, pk=None):
        """Full task detail with history, comments, and spec_title — fetched on demand."""
        task = get_object_or_404(
            Task.objects.select_related("assignee", "spec")
            .prefetch_related("labels", "history", "comments"),
            pk=pk,
        )
        data = TaskDetailSerializer(task, context={"request": request}).data
        schedule_run_id = request.query_params.get("schedule_run_id")
        if not schedule_run_id and task.schedule_id:
            schedule_run_id = str(task.current_schedule_run_id or task.schedule.last_released_run_id or "")
        if schedule_run_id:
            comments = task.comments.filter(schedule_run_id=schedule_run_id)
            histories = task.history.filter(schedule_run_id=schedule_run_id)
            data["comments"] = TaskCommentSerializer(comments, many=True, context={"request": request}).data
            data["history"] = TaskHistorySerializer(histories, many=True).data
        if task.schedule_id:
            data["schedule_runs"] = list(TaskScheduleRun.objects.filter(task=task).order_by("-scheduled_for_utc").values(
                "id", "run_number", "scheduled_for_utc", "released_at_utc", "finished_at_utc",
                "status", "terminal_task_status", "result_summary",
            ))
        return Response(data)

    @action(detail=True, methods=["get"], url_path="ide-options")
    def ide_options(self, request, pk=None):
        task = get_object_or_404(Task.objects.select_related("board"), pk=pk)
        _, settings_obj = _user_settings_for_request(request)
        project_root = _board_project_root(task)
        detected_ides = detect_supported_ides()
        preferred_ide_id = settings_obj.preferred_ide_id if settings_obj else None
        return Response({
            "project_root": project_root,
            "preferred_ide_id": preferred_ide_id,
            "detected_ides": [
                {"id": ide.id, "label": ide.label, "icon_key": ide.icon_key}
                for ide in detected_ides
            ],
            "has_configured_ide": bool(preferred_ide_id),
        })

    @action(detail=True, methods=["post"], url_path="open-project")
    def open_project(self, request, pk=None):
        task = get_object_or_404(Task.objects.select_related("board"), pk=pk)
        project_root = _board_project_root(task)
        if not project_root:
            return Response({"code": "no_project_root", "detail": "Board has no configured project root."}, status=status.HTTP_409_CONFLICT)

        _, settings_obj = _user_settings_for_request(request)
        preferred_ide_id = settings_obj.preferred_ide_id if settings_obj else None
        if not preferred_ide_id:
            return Response({"code": "no_preferred_ide", "detail": "No preferred IDE is configured."}, status=status.HTTP_409_CONFLICT)

        ser = OpenProjectSerializer(data=request.data or {})
        ser.is_valid(raise_exception=True)
        requested_ide_id = (ser.validated_data.get("ide_id") or "").strip().lower() or preferred_ide_id
        if requested_ide_id != preferred_ide_id:
            return Response({"code": "no_preferred_ide", "detail": "Only the configured IDE can be used for Open Project."}, status=status.HTTP_409_CONFLICT)

        ide = get_supported_ide(preferred_ide_id)
        if ide is None:
            return Response({"code": "no_preferred_ide", "detail": "Configured IDE is unsupported."}, status=status.HTTP_409_CONFLICT)
        if ide.detect_launcher() is None:
            return Response({"code": "preferred_ide_not_detected", "detail": f"{ide.label} is no longer detected on this system."}, status=status.HTTP_409_CONFLICT)

        try:
            ide.launch(project_root)
        except Exception as exc:
            return Response({"code": "launch_failed", "detail": str(exc) or "Failed to launch project."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({"ok": True, "ide_id": ide.id, "project_root": project_root})

    @action(detail=True, methods=["post"], url_path="reflect")
    def reflect(self, request, pk=None):
        """Trigger a reflection audit on a completed task."""
        task = self.get_object()
        if task.status not in (TaskStatus.REVIEW, TaskStatus.DONE, TaskStatus.FAILED):
            return Response(
                {"error": "Task must be in REVIEW, DONE, or FAILED status"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ser = ReflectionRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        reviewer_agent = ser.validated_data["reviewer_agent"]
        reviewer_model = ser.validated_data["reviewer_model"]
        # Manual endpoint honors the caller's explicit agent/model. Auto-
        # reflection is the path that applies size-bucketed / reviewer-order
        # selection; the manual endpoint is for operators explicitly
        # requesting a specific reviewer. The env-var forced-provider knob
        # deliberately plays no role in reviewer selection (task: remove
        # env-forced provider from reviewer selection).
        raw = request.data or {}
        req_agent = (raw.get("reviewer_agent") or "").strip()
        req_model = (raw.get("reviewer_model") or "").strip()
        if req_agent or req_model:
            # Caller is pinning a specific reviewer: validate the (agent,
            # model) pair against the named agent's available_models so a
            # typo returns 400 listing valid choices instead of silently
            # burning tokens on a model the agent can't run. (task #325)
            if req_model:
                valid_models = _agent_available_model_names(reviewer_agent)
                if valid_models is not None and reviewer_model not in valid_models:
                    allowed = ", ".join(valid_models) if valid_models else (
                        "(agent carries no advertised models)"
                    )
                    return Response(
                        {
                            "error": "invalid_reviewer_model",
                            "detail": (
                                f"Model {reviewer_model!r} is not in agent "
                                f"{reviewer_agent!r}'s available_models. "
                                f"Allowed for {reviewer_agent}: {allowed}."
                            ),
                            "allowed_models": valid_models,
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            selection_reason = "caller_override"
        else:
            # No reviewer specified — derive from the same deterministic
            # selection auto-reflection uses, so a single-provider board
            # (e.g. codex-only) doesn't crash on a hardcoded claude default.
            # Falls back to the serializer default when no agent users exist
            # (backward compat).
            selection_reason = "manual_default"
            derived_agent, derived_model, _reason = select_reviewer_by_context_size(
                task, board=task.board,
            )
            if derived_agent and derived_model:
                reviewer_agent = derived_agent
                reviewer_model = derived_model

        report = ReflectionReport.objects.create(
            task=task,
            reviewer_agent=reviewer_agent,
            reviewer_model=reviewer_model,
            custom_prompt=ser.validated_data["custom_prompt"],
            context_selections=ser.validated_data["context_selections"],
            requested_by=(
                getattr(request.user, "email", None)
                or getattr(request.user, "username", None)
                or ser.validated_data.get("requested_by")
                or "unknown@user"
            ),
            status=ReflectionStatus.PENDING,
            selection_reason=selection_reason,
        )

        from .dag_executor import execute_reflection
        execute_reflection.delay(report.id)

        return Response(
            ReflectionReportSerializer(report).data,
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["get"], url_path="reflections")
    def reflections(self, request, pk=None):
        """List all reflection reports for a task."""
        task = self.get_object()
        reports = task.reflections.all()
        return Response(ReflectionReportSerializer(reports, many=True).data)


class ReflectionReportViewSet(viewsets.GenericViewSet):
    queryset = ReflectionReport.objects.select_related("task").all()

    def list(self, request):
        """List all reflection reports, with optional status/verdict/board filters."""
        qs = self.get_queryset()
        if status_filter := request.query_params.get("status"):
            qs = qs.filter(status=status_filter)
        if verdict_filter := request.query_params.get("verdict"):
            qs = qs.filter(verdict=verdict_filter)
        if board_filter := request.query_params.get("board"):
            qs = qs.filter(task__board_id=board_filter)
        return Response(ReflectionReportSerializer(qs, many=True).data)

    def retrieve(self, request, pk=None):
        """Get a single reflection report by ID."""
        report = self.get_object()
        return Response(ReflectionReportSerializer(report).data)

    def partial_update(self, request, pk=None):
        """Odin submits reflection results here."""
        report = self.get_object()
        ser = ReflectionReportUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        from django.utils import timezone

        # Only update fields that were explicitly sent in the request
        for field in request.data.keys():
            if field in ser.validated_data:
                value = ser.validated_data[field]
                if value is not None:
                    setattr(report, field, value)

        # Only set completed_at on terminal status transitions
        new_status = ser.validated_data.get("status")
        if new_status in ("COMPLETED", "FAILED"):
            report.completed_at = timezone.now()

        report.save()

        # Error ledger (task #222): a reviewer that emits a non-canonical
        # verdict (anything other than PASS/NEEDS_WORK/FAIL) is the "no
        # verdict in output" failure case from the error ledger — the
        # reviewer ran but produced prose where the harness expected a
        # fenced JSON verdict. Capture so the operator can triage.
        _canonical_verdicts = {"PASS", "NEEDS_WORK", "FAIL"}
        if new_status == "COMPLETED":
            _verdict_norm = (report.verdict or "").upper().strip()
            if _verdict_norm not in _canonical_verdicts:
                try:
                    from .errors import record_reflection_error
                    record_reflection_error(
                        reflection_id=report.id,
                        task_id=report.task_id,
                        symptom=(
                            report.verdict_summary
                            or f"Reflection {report.id} completed with unrecognized verdict {_verdict_norm!r}"
                        ),
                        reviewer_agent=report.reviewer_agent or "",
                        reviewer_model=report.reviewer_model or "",
                        source_id=str(report.id),
                    )
                except Exception:
                    logger.exception(
                        "error ledger: failed to record reflection_error for report %s",
                        report.id,
                    )

        # Post a reflection summary comment on the task when completed
        if new_status == "COMPLETED" and report.verdict_summary:
            verdict_label = (report.verdict or "").upper()
            comment_content = f"**Reflection: {verdict_label}**\n\n{report.verdict_summary}"
            TaskComment.objects.create(
                task=report.task,
                schedule_run=report.task.current_schedule_run,
                # Attribution (task #250): the verdict speaks AS the reviewer,
                # never as whoever requested the run — a requester-attributed
                # verdict once triggered the reply-resume flow as if a human
                # had spoken.
                author_email=(
                    f"{report.reviewer_agent}+{report.reviewer_model}@odin.agent"
                    if report.reviewer_agent and report.reviewer_model
                    else "system@odin.agent"
                ),
                author_label=f"{report.reviewer_agent}/{report.reviewer_model}",
                content=comment_content,
                comment_type=CommentType.REFLECTION,
                attachments=[{
                    "type": "reflection",
                    "report_id": report.id,
                    "verdict": report.verdict,
                }],
            )

        # Auto-advance: PASS verdict triggers merge, which advances
        # REVIEW → TESTING only after merge completes.  This prevents
        # downstream tasks from forking the spec branch before
        # upstream code has landed.
        if (
            new_status == "COMPLETED"
            and report.verdict
            and report.verdict.upper() == "PASS"
        ):
            task = report.task
            task.refresh_from_db(fields=["status"])
            if task.status == TaskStatus.REVIEW:
                _merge_task_on_reflection_pass(task)
            else:
                # Silent-skip regression (task #171): if the task already
                # moved past REVIEW (manual transition, finalize-before-merge,
                # a previous reflection-PASS chain, etc.), the merge dispatch
                # would skip without logging. Surface it so the operator
                # knows the reflection PASSed but the merge never fired.
                logger.warning(
                    "[task:%s] reflection %s: PASS but task is in %s (not REVIEW) "
                    "— merge dispatch skipped (status-not-review)",
                    task.id, report.id, task.status,
                )
                _record_merge_skip(
                    task,
                    f"status_not_review:{task.status}",
                    f"Merge dispatch skipped: reflection verdict was PASS but "
                    f"task is in `{task.status}` (not REVIEW). This usually "
                    f"means the task was already moved past REVIEW by another "
                    f"flow (e.g. spec finalization, manual transition, or a "
                    f"previous reflection-PASS chain). If the spec branch is "
                    f"missing this task's code, merge it manually.",
                )

        # Auto-advance: NEEDS_WORK or FAIL verdict retries or fails after 3 attempts
        verdict = (report.verdict or "").upper()
        if (
            new_status == "COMPLETED"
            and verdict in ("NEEDS_WORK", "FAIL")
        ):
            task = report.task
            task.refresh_from_db(fields=["status"])
            # Mistakes ledger (task #223): distill this verdict to one line so
            # future similar tasks carry the warning. Idempotent per report.
            record_reflection_mistake(report)
            if task.status == TaskStatus.REVIEW:
                # Only genuine verdicts count toward the 3-strike limit.
                # ERROR (reviewer failure) and REVIEWER_INFRA (truncated
                # output) are infra, not judgments — they must not inflate
                # the count (task #346: 1 infra + 2 NEEDS_WORK wrongly
                # FAILED the task on the 2nd NEEDS_WORK).
                completed_count = ReflectionReport.objects.filter(
                    task=task, status=ReflectionStatus.COMPLETED,
                ).exclude(
                    verdict__in=["ERROR", "REVIEWER_INFRA"],
                ).count()

                if completed_count >= 3:
                    # 3 strikes — fail the task (no merge; branch preserved for inspection).
                    # Task #359: the banner must describe the cap, not a stale
                    # failure_type left over from an earlier execution. The
                    # previous shape of this block did NOT touch metadata, so
                    # the frontend banner kept showing whichever reason an
                    # earlier EXECUTING run had stamped (task #356 showed a
                    # 3-day-old stale reap). The cap is the cause now — stamp
                    # it explicitly and overwrite the previous metadata.
                    from .failure_messages import (
                        humanize_failure_reason,
                        suggested_action_for_metadata,
                        write_failure_metadata,
                    )

                    old_status = task.status
                    task.status = TaskStatus.FAILED

                    metadata = dict(task.metadata or {})
                    # The latest report's verdict_summary is the most useful
                    # hook for the human-readable reason — the reviewer said
                    # *why* they rejected the work this round.
                    latest_summary = (report.verdict_summary or "").strip() if report else ""
                    reason_parts = [
                        f"The reviewer rejected this work three times in a row"
                        f" ({verdict} on attempt {completed_count}).",
                    ]
                    if latest_summary:
                        reason_parts.append(f"Latest reviewer note: {latest_summary}")
                    reason_parts.append(
                        "Read the latest reviewer's note before retrying.",
                    )
                    raw_reason = " ".join(reason_parts)
                    human_reason = humanize_failure_reason(
                        raw_reason,
                        failure_type="review_cap",
                        failure_class="review_cap",
                        failure_origin="taskit_views",
                    )
                    write_failure_metadata(
                        metadata,
                        failure_type="review_cap",
                        failure_reason=human_reason,
                        failure_origin="taskit_views",
                        failure_class="review_cap",
                    )
                    # Stamp the policy metadata so the audit trail mirrors
                    # what apply_failure_policy would have written for any
                    # other HUMAN class. We don't dispatch through the policy
                    # engine here because the cap already has a richer,
                    # plain-English comment and the HUMAN policy would only
                    # add a generic audit note on top — the operator reads
                    # the rich comment first.
                    metadata["policy_class"] = "review_cap"
                    metadata["policy_action"] = "human"
                    metadata["policy_at"] = timezone.now().isoformat()
                    task.metadata = metadata
                    task.kanban_position = move_task(
                        task, target_status=TaskStatus.FAILED, target_index=None,
                    )
                    task.save(update_fields=[
                        "status", "kanban_position", "metadata", "last_updated_at",
                    ])
                    TaskHistory.objects.create(
                        task=task,
                        schedule_run=task.current_schedule_run,
                        field_name="status",
                        old_value=old_status,
                        new_value=TaskStatus.FAILED,
                        changed_by="system@taskit",
                    )
                    suggested_action = suggested_action_for_metadata(metadata)
                    # The suggested action already leads with the cause
                    # ("The reviewer rejected this work three times…");
                    # the comment just needs to add the verdict + attempt
                    # context that is unique to this run and point at the
                    # next step.
                    attempt_label = f" ({verdict} on attempt {completed_count})"
                    comment_body = (
                        f"Review cap reached{attempt_label}. "
                        f"{suggested_action}"
                    )
                    TaskComment.objects.create(
                        task=task,
                        schedule_run=task.current_schedule_run,
                        author_email="system@taskit",
                        author_label="system",
                        content=comment_body,
                        comment_type=CommentType.STATUS_UPDATE,
                    )
                    maybe_finalize_schedule_run(task, TaskStatus.FAILED)
                    logger.info(
                        "Task %s FAILED after %d reflection attempts without passing",
                        task.id, completed_count,
                    )
                else:
                    # Capture pre-rework fields so the continuity audit can compare
                    # against whatever `_maybe_reassign_on_quota_failure` left in
                    # place. F45 mandate: rework keeps agent+model by default; the
                    # audit trail must show the continuity decision even when the
                    # values did not change.
                    task.refresh_from_db(fields=["assignee_id", "model_name", "metadata"])
                    pre_rework_assignee_id = task.assignee_id
                    pre_rework_model = task.model_name

                    # Check if this failure was quota/rate-limit related
                    # and reassign to a different agent if so. The returned
                    # bool signals whether the quota path surfaced its own
                    # explanation (a reassignment comment, or a "no alternative
                    # agent available" warning) — when True, the continuity
                    # helper below suppresses its duplicate comment so the
                    # operator's most-recent comment matches reality.
                    quota_handled = _maybe_reassign_on_quota_failure(task, report)

                    # Capability failure = repeated review rejection (task #328).
                    # A quota failure is infra, not capability, so it never
                    # counts toward escalation. Once the reviewer has rejected
                    # the work `capability_escalate_after` times (default 2),
                    # escalate exactly ONE deliberate tier — the sanctioned
                    # tier jump. The FIRST rejection keeps the same agent
                    # (continuity below); only the repeat escalates.
                    capability_escalated = False
                    if not quota_handled:
                        from .failure_policy import capability_policy
                        cap = capability_policy(task.board)
                        if cap.enabled and completed_count >= cap.escalate_after:
                            task.metadata = dict(task.metadata or {})
                            task.metadata["capability_rejections"] = completed_count
                            if _maybe_escalate_model(task):
                                capability_escalated = True
                                task.save(update_fields=[
                                    "assignee", "model_name", "metadata",
                                ])

                    # F45 continuity record: if assignee+model survived the rework
                    # transition, write history + comment + metadata so the choice
                    # is visible. The quota path writes its own history rows
                    # inside `_maybe_reassign_on_quota_failure`, so we skip when
                    # anything actually moved.
                    _record_rework_continuity(
                        task,
                        pre_rework_assignee_id,
                        pre_rework_model,
                        suppress_comment=quota_handled or capability_escalated,
                    )

                    # Send back for another execution attempt
                    old_status = task.status
                    task.status = TaskStatus.IN_PROGRESS
                    # Task #353: scrub any stale dispatch_blocked_reason stamp
                    # left by a previous gate BEFORE saving the new status.
                    # If the gate below re-holds the task, _set_* will re-stamp
                    # with the live holder list. Without this clear, the
                    # banner shows "memory budget full" on a task that has
                    # just been requeued and dispatched.
                    task_metadata = dict(task.metadata or {})
                    for stale_key in (
                        "dispatch_blocked_reason",
                        "dispatch_blocked_at",
                        "dispatch_blocked_blocked_by",
                    ):
                        task_metadata.pop(stale_key, None)
                    task.metadata = task_metadata
                    task.save(update_fields=["status", "metadata"])
                    TaskHistory.objects.create(
                        task=task,
                        schedule_run=task.current_schedule_run,
                        field_name="status",
                        old_value=old_status,
                        new_value=TaskStatus.IN_PROGRESS,
                        changed_by="system@taskit",
                    )
                    logger.info(
                        "Auto-advanced task %s from REVIEW → IN_PROGRESS after reflection %s (attempt %d)",
                        task.id, verdict, completed_count,
                    )
                    # Fire execution strategy (mirrors TaskViewSet.update lines
                    # 746-756), but gate on DAG_EXECUTOR_MAX_CONCURRENCY so
                    # reflection-ordered rework can't re-enter EXECUTING past the
                    # cap. At capacity the task stays IN_PROGRESS (queued) and
                    # poll_and_execute dispatches it when a slot frees — the same
                    # slot accounting that gates fresh dispatches.
                    if task.assignee_id:
                        max_concurrency = getattr(
                            settings, "DAG_EXECUTOR_MAX_CONCURRENCY", 3,
                        )
                        executing_count = Task.objects.filter(
                            status=TaskStatus.EXECUTING,
                        ).count()
                        from .dag_executor import (
                            _clear_dispatch_blocked_reason,
                            _set_dispatch_blocked_reason,
                        )
                        from .sandbox_budget import (
                            default_vm_mem_mib, memory_share_holders, spawn_fits,
                        )
                        if executing_count >= max_concurrency:
                            _set_dispatch_blocked_reason(task, "concurrency_cap_reached")
                            logger.info(
                                "Rework task %s held queued: %d EXECUTING at cap %d",
                                task.id, executing_count, max_concurrency,
                            )
                        elif not spawn_fits(default_vm_mem_mib()):
                            # Memory budget gate — mirrors poll_and_execute: a
                            # rework spawn that would exceed the shared global
                            # budget waits in line instead of booting into swap.
                            # Holder list comes from the same primitive the
                            # dispatcher uses, so the banner names the same
                            # tasks the badge / factory / /factory do.
                            _set_dispatch_blocked_reason(
                                task,
                                "memory_budget_full",
                                blocked_by=memory_share_holders(),
                            )
                            logger.info(
                                "Rework task %s held queued: memory budget full",
                                task.id,
                            )
                        else:
                            # Gate didn't hold → make sure no stale stamp is
                            # still hanging on. The save above already cleared
                            # the metadata key, but defence-in-depth costs
                            # nothing and survives a future refactor that
                            # reaches this branch without first saving.
                            _clear_dispatch_blocked_reason(task)
                            from .execution import get_strategy
                            strategy = get_strategy()
                            if strategy:
                                logger.info(
                                    "Firing execution strategy for task %s after %s retry",
                                    task.id, verdict,
                                )
                                strategy.trigger(task)

        # Auto-retry: REVIEWER_INFRA verdict (truncated review output) does
        # NOT count against the 3-strike limit. The reviewer ran out of
        # tokens before emitting a verdict — that's infra, not a judgment.
        # Retry with a fresh reviewer up to REVIEWER_INFRA_RETRY_CAP times,
        # then park for manual triage. (Task #346: reflection 360 on task
        # 342 had real reasoning but no JSON verdict — the whole review
        # run's tokens were spent for nothing.)
        if (
            new_status == "COMPLETED"
            and verdict == "REVIEWER_INFRA"
        ):
            task = report.task
            task.refresh_from_db(fields=["status", "metadata"])
            if task.status == TaskStatus.REVIEW:
                metadata = dict(task.metadata or {})
                infra_count = int(metadata.get("reviewer_infra_retry_count", 0))
                if infra_count < REVIEWER_INFRA_RETRY_CAP:
                    metadata["reviewer_infra_retry_count"] = infra_count + 1
                    task.metadata = metadata
                    task.save(update_fields=["metadata"])
                    logger.info(
                        "[task:%s] REVIEWER_INFRA (truncated review) — "
                        "triggering fresh reflection retry %d/%d",
                        task.id, infra_count + 1, REVIEWER_INFRA_RETRY_CAP,
                    )
                    _trigger_auto_reflection(
                        task,
                        exclude_reviewers=frozenset({
                            (report.reviewer_agent, report.reviewer_model),
                        }),
                    )
                else:
                    TaskComment.objects.create(
                        task=task,
                        schedule_run=task.current_schedule_run,
                        author_email="system@taskit",
                        author_label="system",
                        content=(
                            f"Reviewer output truncated {infra_count} times "
                            f"(infra retry cap {REVIEWER_INFRA_RETRY_CAP} reached). "
                            f"Task left in REVIEW for manual triage — the reviewer "
                            f"kept running out of tokens before emitting a verdict."
                        ),
                        comment_type=CommentType.STATUS_UPDATE,
                    )
                    logger.warning(
                        "[task:%s] REVIEWER_INFRA retry cap (%d) reached — "
                        "leaving in REVIEW for manual triage",
                        task.id, REVIEWER_INFRA_RETRY_CAP,
                    )

        return Response(ReflectionReportSerializer(report).data)

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        """Cancel a PENDING or RUNNING reflection."""
        from django.utils import timezone

        report = self.get_object()
        if report.status not in (ReflectionStatus.PENDING, ReflectionStatus.RUNNING):
            return Response(
                {"error": "Can only cancel PENDING or RUNNING reflections"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        report.status = ReflectionStatus.FAILED
        report.error_message = "Cancelled by user"
        report.completed_at = timezone.now()
        report.save(update_fields=["status", "error_message", "completed_at"])
        return Response(ReflectionReportSerializer(report).data)

    def destroy(self, request, pk=None):
        """Delete a reflection report."""
        report = self.get_object()
        report.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SpecViewSet(viewsets.ModelViewSet):
    serializer_class = SpecSerializer
    pagination_class = StandardPagination

    def _ensure_board_working_dir(self, board):
        working_dir = (board.working_dir or "").strip()
        if not working_dir:
            raise ValidationError({"board_id": "Board has no working directory."})
        base_path = Path(working_dir).resolve()
        if not base_path.exists() or not base_path.is_dir():
            raise ValidationError({"board_id": "Board working directory is unavailable on disk."})
        if not os.access(str(base_path), os.W_OK):
            raise ValidationError({"board_id": "Board working directory is not writable."})
        return base_path

    def _managed_spec_target_path(self, board, file_name):
        base_path = self._ensure_board_working_dir(board)
        normalized_name = _normalize_managed_file_name(file_name)
        target_path = (base_path / normalized_name).resolve()
        if target_path.parent != base_path:
            raise ValidationError({"file_name": "Managed specs must stay in the board root."})
        return base_path, target_path, normalized_name

    def _managed_spec_metadata(self, spec, file_name):
        metadata = dict(spec.metadata or {})
        metadata[MANAGED_SPEC_PATH_KEY] = file_name
        metadata.setdefault("working_dir", spec.board.working_dir)
        metadata["managed_origin"] = "taskit_ui"
        return metadata

    def _write_managed_spec_file(self, path_obj, content):
        try:
            path_obj.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise ValidationError({"content": f"Failed to write spec file: {exc}"})

    
    def get_queryset(self):
        query_params = self.request.query_params
        qs = Spec.objects.annotate(task_count=Count("tasks", distinct=True))

        board_ids = _parse_multi_values(query_params, "board_id", aliases=("board",))
        if board_ids:
            qs = qs.filter(board_id__in=board_ids)

        odin_id = query_params.get("odin_id")
        if odin_id:
            qs = qs.filter(odin_id=odin_id)

        search = query_params.get("search") or query_params.get("q")
        if search:
            qs = qs.filter(Q(title__icontains=search) | Q(content__icontains=search))

        status_values = [s.lower() for s in _parse_multi_values(query_params, "status")]
        if status_values:
            # Planning-specific statuses use the new status field
            new_status_values = [
                s for s in status_values
                if s in (Spec.STATUS_PLANNING, Spec.STATUS_PLANNING_COMPLETE, Spec.STATUS_PLANNING_FAILED)
            ]
            if new_status_values:
                qs = qs.filter(status__in=new_status_values)
            # Legacy: "active" → abandoned=False, "abandoned" → abandoned=True
            abandoned_values = []
            if "abandoned" in status_values:
                abandoned_values.append(True)
            if "active" in status_values:
                abandoned_values.append(False)
            if abandoned_values:
                qs = qs.filter(abandoned__in=abandoned_values)

        abandoned = query_params.get("abandoned")
        if abandoned is not None:
            qs = qs.filter(abandoned=abandoned.lower() in ("true", "1"))

        qs = _apply_date_range(qs, query_params, "created_at", "created_from", "created_to")

        tokens = _parse_sort_tokens(
            query_params.get("sort"),
            {"created_at", "title", "task_count"},
            default_tokens=[("created_at", True)],
        )
        qs = qs.order_by(*_build_order_by(tokens, {
            "created_at": "created_at",
            "title": "title",
            "task_count": "task_count",
        }))

        # Exclude temporary specs created by `odin plan` during UI planning
        # sessions.  The consumer writes the spec content to a temp file named
        # spec_{pk}_{random}.md and odin registers a new spec using that file
        # path as the title.  These byproduct specs are merged into the UI
        # planning spec (and deleted) when planning completes — but while
        # planning is running they would otherwise clutter the list.
        # Only exclude on list views — detail/update/delete must always find
        # the spec by PK, and odin_id lookups need exact matches for upsert.
        if self.action == "list" and not odin_id:
            qs = qs.exclude(title__regex=r'spec_\d+_[a-z0-9_]+\.md$')

        return qs

    def get_serializer_class(self):
        if self.action == "create":
            if "planner_config" in self.request.data or "plannerConfig" in self.request.data:
                return CreatePlanningSpecSerializer
            return CreateSpecSerializer
        if self.action == "list":
            return SpecListSerializer
        return SpecSerializer

    def create(self, request, *args, **kwargs):
        is_planning = "planner_config" in request.data or "plannerConfig" in request.data
        if is_planning:
            serializer = CreatePlanningSpecSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            spec = serializer.save(
                odin_id=f"ui-planning-{uuid.uuid4().hex[:12]}",
                source="ui",
                status=Spec.STATUS_PLANNING,
            )
        else:
            serializer = CreateSpecSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            spec = serializer.save()
        return Response(
            SpecSerializer(spec).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"], url_path="planning-complete")
    def mark_planning_complete(self, request, pk=None):
        """Mark a planning spec as complete (called internally after planning finishes)."""
        spec = self.get_object()
        spec.status = Spec.STATUS_PLANNING_COMPLETE
        spec.save(update_fields=["status"])
        return Response({"status": "ok"})

    @action(detail=True, methods=["post"], url_path="planning-failed")
    def mark_planning_failed(self, request, pk=None):
        """Mark a planning spec as failed (called by CLI when planning crashes)."""
        spec = self.get_object()
        spec.status = Spec.STATUS_PLANNING_FAILED
        spec.save(update_fields=["status"])
        return Response({"status": "ok"})

    @action(detail=True, methods=["post"], url_path="retry-planning")
    def retry_planning(self, request, pk=None):
        """Reset a failed planning spec back to planning so the terminal can reconnect."""
        spec = self.get_object()
        if spec.status not in (Spec.STATUS_PLANNING_FAILED, Spec.STATUS_PLANNING):
            return Response(
                {"error": "Can only retry specs with status planning_failed"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        spec.status = Spec.STATUS_PLANNING
        spec.save(update_fields=["status"])
        return Response(SpecSerializer(spec).data)

    @action(detail=True, methods=["post"], url_path="request-board-plan")
    def request_board_plan(self, request, pk=None):
        """Trigger board-driven planning: dispatches a Celery task that runs
        ``odin plan --board-driven`` in the worker sandbox.

        The gate questions, summary, and preview land as SpecComments
        (comment_type=QUESTION / STATUS_UPDATE). A human replies via
        POST /specs/:id/comments/, which triggers the resume phase.
        """
        spec = self.get_object()
        meta = dict(spec.metadata or {})
        meta["board_plan_status"] = "requested"
        spec.metadata = meta
        spec.status = Spec.STATUS_PLANNING
        spec.save()

        from .board_planner import run_board_driven_plan
        run_board_driven_plan.delay(spec.id)
        return Response({"status": "ok", "spec_id": spec.id})

    def update(self, request, *args, **kwargs):
        spec = self.get_object()
        title = request.data.get("title")
        content = request.data.get("content")
        abandoned = request.data.get("abandoned")
        metadata = request.data.get("metadata")

        if title is not None:
            spec.title = title
        if content is not None:
            spec.content = content
        if abandoned is not None:
            spec.abandoned = abandoned
        if metadata is not None:
            spec.metadata = metadata
        spec.save()
        return Response(SpecSerializer(spec).data)


    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        """Transition a planning_complete spec to active so execution can begin."""
        spec = self.get_object()
        if spec.status != Spec.STATUS_PLANNING_COMPLETE:
            return Response(
                {"error": "Can only activate specs with status planning_complete"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        spec.status = Spec.STATUS_ACTIVE
        spec.save(update_fields=["status"])
        return Response(SpecSerializer(spec).data)

    @action(detail=True, methods=["get"], url_path="diagnostic")
    def diagnostic(self, request, pk=None):
        """Full diagnostic view: spec + all tasks with history, comments, and metadata."""
        spec = get_object_or_404(
            Spec.objects.select_related("board").prefetch_related(
                "tasks__assignee",
                "tasks__labels",
                "tasks__history",
                "tasks__comments",
                "comments",
            ),
            pk=pk,
        )
        return Response(SpecDiagnosticSerializer(spec, context={"request": request}).data)

    @action(detail=True, methods=["get"], url_path="story")
    def story(self, request, pk=None):
        """Wave story: per-task narrative — dispatch time, agent+model,
        redo rounds, merge mode/conflicts, tokens/cost, duration, status,
        and the newest human-relevant comment. Shared builder with
        testing_tools/spec_trace.py (see tasks/spec_story.py) so the CLI
        diagnostic and this endpoint never drift.
        """
        spec = get_object_or_404(Spec.objects.select_related("board"), pk=pk)
        return Response(build_spec_story(spec))

    @action(detail=True, methods=["post"])
    def clone(self, request, pk=None):
        from django.db import transaction

        # Spec-only keys tied to a specific execution/worktree that must NOT
        # carry over to the clone. Task-level execution keys are shared with
        # the schedule-materialize path via EXECUTION_SCOPED_METADATA_KEYS so
        # "execution-scoped" has one definition across the codebase.
        SPEC_METADATA_STRIP_KEYS = {
            "branch", "worktree_path", "planning_trace", "pr_url",
            "finalized_at",
        }
        TASK_METADATA_STRIP_KEYS = {
            "branch", "worktree_path", "working_dir", "merge_status",
            "started_at", "tmux_session", "last_duration_ms", "full_output",
            "taskit_id", "diff_stat", "subprocess_pid", "trace_file",
            "active_execution", "worktree_status", "worktree_error",
            "last_failure_type", "last_failure_reason", "last_failure_origin",
            "failure_class",
            "escalation_history", "escalation_count", "escalation_max",
        }

        with transaction.atomic():
            spec = get_object_or_404(Spec, pk=pk)

            # 1. Clone the Spec
            import time
            timestamp = int(time.time())
            new_odin_id = f"{spec.odin_id}_copy_{timestamp}"

            # Ensure uniqueness
            if Spec.objects.filter(odin_id=new_odin_id).exists():
                new_odin_id = f"{spec.odin_id}_{timestamp}_2"

            clean_spec_metadata = {
                k: v for k, v in (spec.metadata or {}).items()
                if k not in SPEC_METADATA_STRIP_KEYS
            }

            new_spec = Spec.objects.create(
                odin_id=new_odin_id,
                title=f"{spec.title} (CLONE)",
                source=spec.source,
                content=spec.content,
                abandoned=False,  # Reset abandoned status
                board=spec.board,
                metadata=clean_spec_metadata,
            )

            # 2. Clone associated Tasks
            tasks = Task.objects.filter(spec=spec).order_by('id')
            created_by = "admin@example.com"
            if request.user and hasattr(request.user, 'email') and request.user.email:
                created_by = request.user.email
            elif spec.board.tasks.exists():
                first_task = spec.board.tasks.first()
                if first_task and first_task.created_by:
                    created_by = first_task.created_by

            old_id_to_new_id = {}
            new_tasks_needing_remap = []

            for task in tasks:
                clean_task_metadata = {
                    k: v for k, v in (task.metadata or {}).items()
                    if k not in EXECUTION_SCOPED_METADATA_KEYS
                }

                new_task = Task.objects.create(
                    board=task.board,
                    title=f"(CLONE) {task.title}",
                    description=task.description,
                    dev_eta_seconds=task.dev_eta_seconds,
                    assignee=task.assignee,
                    priority=task.priority,
                    status="TODO",
                    created_by=created_by,
                    spec=new_spec,
                    depends_on=[],
                    complexity=task.complexity,
                    metadata=clean_task_metadata,
                    skip_reflection=task.skip_reflection,
                )
                old_id_to_new_id[str(task.id)] = str(new_task.id)
                if task.depends_on:
                    new_tasks_needing_remap.append((new_task, task.depends_on))

                # Copy M2M labels
                new_task.labels.set(task.labels.all())

                # Create history
                TaskHistory.objects.create(
                    task=new_task,
                    field_name="created",
                    old_value="",
                    new_value=f"Task cloned from {task.id}",
                    changed_by=created_by,
                )

            # Remap depends_on: replace original task IDs with cloned IDs.
            # IDs not in the map (external deps) are preserved as-is.
            for new_task, original_deps in new_tasks_needing_remap:
                new_task.depends_on = [
                    old_id_to_new_id.get(dep_id, dep_id)
                    for dep_id in original_deps
                ]
                new_task.save(update_fields=["depends_on"])

        return Response(SpecSerializer(new_spec).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="planning_result")
    def planning_result(self, request, pk=None):
        """Record a planning trace — stores metadata and creates SpecComment."""
        spec = get_object_or_404(Spec, pk=pk)
        ser = PlanningResultSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        # Store planning trace metadata on spec
        spec_meta = spec.metadata or {}
        spec_meta["planning_trace"] = {
            "agent": d["agent"],
            "model": d["model"],
            "duration_ms": d["duration_ms"],
            "success": d["success"],
        }
        if d.get("token_usage"):
            spec_meta["planning_trace"]["token_usage"] = d["token_usage"]
        if d.get("effective_input"):
            spec_meta["planning_trace"]["effective_input"] = d["effective_input"][:5000]
        spec.metadata = spec_meta
        spec.save()

        # Compose comment text
        duration_s = d["duration_ms"] / 1000
        verb = "Completed" if d["success"] else "Failed"
        comment_text = f"{verb} in {duration_s:.1f}s"
        if d["raw_output"]:
            comment_text += f"\n\n{d['raw_output']}"

        # Create spec comment
        author_email = f"{d['agent']}+{d['model']}@odin.agent"
        SpecComment.objects.create(
            spec=spec,
            author_email=author_email,
            author_label=f"{d['agent']} ({d['model']})",
            content=comment_text,
            comment_type=CommentType.PLANNING,
        )

        # Notify board members about planning result
        from .notification_service import notify
        member_ids = list(
            BoardMembership.objects.filter(board=spec.board).values_list("user_id", flat=True)
        )
        verb = "completed" if d["success"] else "failed"
        notify(
            recipient_ids=member_ids,
            notification_type="planning_complete",
            title=f'Planning {verb} for "{spec.title}"',
            body=f"Agent: {d['agent']}, Duration: {d['duration_ms'] / 1000:.1f}s",
            spec=spec,
            board=spec.board,
            actor_email=author_email,
        )

        return Response(SpecSerializer(spec).data)

    @action(detail=True, methods=["get", "post"], url_path="comments")
    def comments(self, request, pk=None):
        """List or create comments for a spec.

        POST creates a SpecComment — used by humans to reply to board-driven
        planning gate questions, and by odin to post planning updates.
        Accepts ``attachment_ids`` to link previously-uploaded file
        attachments (see the ``attachments`` action) to the new comment.
        """
        spec = get_object_or_404(Spec, pk=pk)
        if request.method == "POST":
            data = request.data.copy() if hasattr(request.data, "copy") else dict(request.data)
            attachment_ids = data.pop("attachment_ids", [])
            ser = SpecCommentSerializer(data=data)
            ser.is_valid(raise_exception=True)
            comment = SpecComment.objects.create(spec=spec, **ser.validated_data)
            if attachment_ids:
                SpecCommentAttachment.objects.filter(
                    id__in=attachment_ids, spec=spec, comment__isnull=True,
                ).update(comment=comment)
            return Response(
                SpecCommentSerializer(comment, context={"request": request}).data,
                status=status.HTTP_201_CREATED,
            )
        qs = SpecComment.objects.filter(spec=spec)
        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(
                SpecCommentSerializer(page, many=True, context={"request": request}).data
            )
        return Response(SpecCommentSerializer(qs, many=True, context={"request": request}).data)

    MAX_SPEC_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10 MB

    @action(detail=True, methods=["post"], url_path="attachments")
    def attachments(self, request, pk=None):
        """Upload a file attachment on a spec (e.g. plan preview HTML).

        Files are stored as :class:`SpecCommentAttachment` rows with
        ``comment=None`` (orphan).  Pass the returned ``id`` in the
        ``attachment_ids`` list when POSTing a spec comment to link the file
        to that comment.
        """
        spec = get_object_or_404(Spec, pk=pk)
        files = request.FILES.getlist("files")
        if not files:
            return Response(
                {"detail": "No files provided. Include one or more 'files' in the upload."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        for f in files:
            if f.size > self.MAX_SPEC_ATTACHMENT_SIZE:
                return Response(
                    {"detail": f"File '{f.name}' exceeds 10 MB limit."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        author_email = request.data.get("author_email", "agent@odin.agent")
        created = []
        for f in files:
            attachment = SpecCommentAttachment.objects.create(
                spec=spec,
                file=f,
                original_filename=f.name,
                content_type=f.content_type or "application/octet-stream",
                file_size=f.size,
                uploaded_by=author_email,
            )
            created.append(attachment)
        serializer = SpecCommentAttachmentSerializer(
            created, many=True, context={"request": request}
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def finalize(self, request, pk=None):
        """Create a PR for a spec by running `odin spec finalize`."""
        spec = get_object_or_404(
            Spec.objects.select_related("board"), pk=pk
        )
        meta = spec.metadata or {}
        odin_id = meta.get("odin_id") or spec.odin_id
        branch = meta.get("branch")

        # Pre-flight: branch required
        if not branch:
            return Response(
                {"error": "Spec has no branch (worktree isolation may be disabled)"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Pre-flight: already has PR
        existing_pr = meta.get("pr_url")
        if existing_pr:
            return Response(
                {"error": "Spec already has a PR", "pr_url": existing_pr},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Pre-flight: board working dir
        working_dir = (spec.board.working_dir or "").strip()
        if not working_dir:
            return Response(
                {"error": "Board has no working directory"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Pre-flight: gh CLI installed and authenticated
        try:
            subprocess.run(
                ["gh", "--version"], capture_output=True, text=True, timeout=5
            )
        except FileNotFoundError:
            return Response(
                {"error": "GitHub CLI (gh) is not installed. Install it: https://cli.github.com"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        gh_auth = subprocess.run(
            ["gh", "auth", "status"], capture_output=True, text=True, timeout=5
        )
        if gh_auth.returncode != 0:
            return Response(
                {"error": "GitHub CLI is not authenticated. Run `gh auth login` first."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Pre-flight: git remote
        try:
            remote_result = subprocess.run(
                ["git", "remote"], capture_output=True, text=True,
                cwd=working_dir, timeout=5,
            )
            if not remote_result.stdout.strip():
                return Response(
                    {"error": "No git remote configured. Run `git remote add origin <url>` first."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except Exception:
            pass  # Non-fatal — let odin handle it

        # Run odin spec finalize
        cli_path = getattr(settings, "ODIN_CLI_PATH", "odin")
        cmd = [cli_path, "spec", "finalize", str(odin_id)]

        try:
            result = subprocess.run(
                cmd, cwd=working_dir, capture_output=True, text=True, timeout=120,
            )
        except FileNotFoundError:
            return Response(
                {"error": "Odin CLI not found. Check ODIN_CLI_PATH setting."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        except subprocess.TimeoutExpired:
            return Response(
                {"error": "PR creation timed out. Try running `odin spec finalize` manually."},
                status=status.HTTP_504_GATEWAY_TIMEOUT,
            )

        if result.returncode != 0:
            # CLI prints errors to stdout via rich console
            combined = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
            # Extract the human-readable part after "PR creation failed: "
            import re
            pr_fail = re.search(r'PR creation failed:\s*(.+)', combined)
            if pr_fail:
                msg = pr_fail.group(1).strip()
            else:
                # Fallback: last non-empty line is usually the most relevant
                lines = [l.strip() for l in combined.splitlines() if l.strip()]
                msg = lines[-1] if lines else "Unknown error"
            return Response(
                {"error": msg},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Re-read spec from DB (odin updates metadata via API)
        spec.refresh_from_db()
        refreshed_meta = spec.metadata or {}
        pr_url = refreshed_meta.get("pr_url")

        # Parse stdout for PR URL as fallback (odin prints it)
        if not pr_url:
            import re
            url_match = re.search(r'https://github\.com/\S+/pull/\d+', result.stdout or "")
            if url_match:
                pr_url = url_match.group(0)
                # Persist it to metadata so it's available on reload
                refreshed_meta["pr_url"] = pr_url
                spec.metadata = refreshed_meta
                spec.save(update_fields=["metadata"])

        if not pr_url:
            logger.warning(
                "odin spec finalize for %s produced no PR URL. stdout: %s",
                odin_id, (result.stdout or "").strip()[:500],
            )
            return Response(
                {"error": "Branches merged but no PR was created. Check that `gh auth login` is configured and the repo has a remote."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response({
            "pr_url": pr_url,
            "finalized_at": refreshed_meta.get("finalized_at"),
        })

    @action(detail=True, methods=["post"], url_path="finalize_tasks")
    def finalize_tasks(self, request, pk=None):
        """Transition all TESTING tasks to DONE after PR creation.

        Called by odin orchestrator after creating the spec PR, or manually.
        Accepts {"pr_url": "https://..."}.  Idempotent.
        """
        from .dag_executor import _transition_spec_tasks_to_done

        spec = get_object_or_404(Spec, pk=pk)
        pr_url = request.data.get("pr_url", "")
        if not pr_url:
            return Response(
                {"error": "pr_url is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Update spec metadata
        meta = dict(spec.metadata or {})
        if not meta.get("pr_url"):
            meta["pr_url"] = pr_url
        if not meta.get("finalized_at"):
            from datetime import datetime, timezone as dt_tz
            meta["finalized_at"] = datetime.now(dt_tz.utc).isoformat()
        if not meta.get("finalized_by"):
            meta["finalized_by"] = "manual"
        spec.metadata = meta
        spec.save(update_fields=["metadata"])

        _transition_spec_tasks_to_done(spec, pr_url)

        return Response({
            "status": "ok",
            "pr_url": pr_url,
        })

    @action(detail=True, methods=["get"])
    def commits(self, request, pk=None):
        """List commits on the spec branch since it diverged from main."""
        spec = get_object_or_404(Spec.objects.select_related("board"), pk=pk)
        meta = spec.metadata or {}
        branch = meta.get("branch")
        if not branch:
            return Response([])

        working_dir = (spec.board.working_dir or "").strip()
        if not working_dir:
            return Response([])

        fmt = "%H%x00%h%x00%s%x00%an%x00%aI"
        cmd = [
            "git", "log", f"main..{branch}",
            f"--format={fmt}", "--reverse",
        ]
        try:
            result = subprocess.run(
                cmd, cwd=working_dir, capture_output=True, text=True, timeout=10,
            )
        except Exception:
            return Response([])

        if result.returncode != 0:
            return Response([])

        commits = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("\x00")
            if len(parts) < 5:
                continue
            commits.append({
                "hash": parts[0],
                "short_hash": parts[1],
                "message": parts[2],
                "author": parts[3],
                "date": parts[4],
            })
        return Response(commits)

    def destroy(self, request, *args, **kwargs):
        spec = self.get_object()
        Task.objects.filter(spec=spec).delete()
        spec.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET"])
def dashboard(request):
    """Single endpoint returning all dashboard data — replaces 4+N individual calls."""
    board_id = request.query_params.get("board_id")

    users = User.objects.all()
    boards = Board.objects.prefetch_related("memberships").all()
    labels = Label.objects.all()

    specs_qs = Spec.objects.all()
    tasks_qs = (
        Task.objects.select_related("assignee")
        .prefetch_related("labels", "history")
        .annotate(comment_count=Count("comments"))
    )
    tasks_qs = _exclude_hidden_scheduled_tasks(tasks_qs)

    if board_id:
        specs_qs = specs_qs.filter(board_id=board_id)
        tasks_qs = tasks_qs.filter(board_id=board_id)

    return Response({
        "users": UserSerializer(users, many=True).data,
        "boards": BoardSerializer(boards, many=True).data,
        "labels": LabelSerializer(labels, many=True).data,
        "specs": SpecSerializer(specs_qs.prefetch_related("tasks__assignee", "tasks__labels"), many=True).data,
        "tasks": TaskDashboardSerializer(tasks_qs, many=True).data,
    })


@api_view(["GET"])
def timeline(request):
    """Timeline feed of tasks with history for timeline/DAG views (unpaginated)."""
    query_params = request.query_params
    qs = (
        Task.objects.select_related("assignee")
        .prefetch_related("labels", "history", "reflections")
        .annotate(comment_count=Count("comments"))
    )
    qs = _exclude_hidden_scheduled_tasks(qs)

    board_ids = _parse_multi_values(query_params, "board_id", aliases=("board",))
    if board_ids:
        qs = qs.filter(board_id__in=board_ids)

    statuses = _parse_multi_values(query_params, "status")
    if statuses:
        qs = qs.filter(status__in=statuses)

    assignee_ids = _parse_multi_values(query_params, "assignee_id", aliases=("assignee",))
    if assignee_ids:
        qs = qs.filter(assignee_id__in=assignee_ids)

    priorities = _parse_multi_values(query_params, "priority")
    if priorities:
        qs = qs.filter(priority__in=priorities)

    search = query_params.get("search") or query_params.get("q")
    if search:
        qs = qs.filter(Q(title__icontains=search) | Q(description__icontains=search))

    qs = _apply_date_range(qs, query_params, "created_at", "date_from", "date_to")
    qs = _apply_date_range(qs, query_params, "created_at", "created_from", "created_to")
    qs = _apply_date_range(qs, query_params, "last_updated_at", "updated_from", "updated_to")

    tokens = _parse_sort_tokens(
        query_params.get("sort"),
        {"created_at", "title"},
        default_tokens=[("created_at", False), ("title", False)],
    )
    qs = qs.order_by(*_build_order_by(tokens, {
        "created_at": "created_at",
        "title": "title",
    }))

    return Response(TaskDashboardSerializer(qs, many=True).data)


@api_view(["GET"])
def kanban(request):
    """Kanban board endpoint with optional per-status pagination.

    Initial load (no ``status`` param):
        GET /api/kanban/?board_id=1&per_status_limit=20
        Returns ``{"columns": {"BACKLOG": {"tasks": [...], "total_count": N}, ...}}``

    Load more (``status`` param present):
        GET /api/kanban/?board_id=1&status=TESTING&offset=20&limit=20
        Returns ``{"tasks": [...], "total_count": N, "has_more": bool}``
    """
    board_id = request.query_params.get("board_id") or request.query_params.get("board")
    query_params = request.query_params
    base_qs = (
        Task.objects.select_related("assignee")
        .prefetch_related(
            "labels", "reflections",
            Prefetch("history", queryset=TaskHistory.objects.filter(field_name="status").order_by("changed_at")),
        )
        .annotate(comment_count=Count("comments"))
    )
    base_qs = _exclude_hidden_scheduled_tasks(base_qs)
    if board_id:
        base_qs = base_qs.filter(board_id=board_id)
    base_qs = _apply_date_range(base_qs, query_params, "created_at", "date_from", "date_to")

    status_param = query_params.get("status")
    if status_param:
        return _kanban_load_more(base_qs, status_param, query_params)
    return _kanban_initial(base_qs, query_params)


def _kanban_initial(base_qs, query_params):
    per_status_limit = min(int(query_params.get("per_status_limit", 20)), 200)
    columns = {}
    for col in KANBAN_COLUMNS:
        col_qs = base_qs.filter(status__in=get_statuses_for_column(col))
        total = col_qs.count()
        col_qs = order_column_queryset(col_qs, col)
        tasks_data = TaskKanbanCardSerializer(col_qs[:per_status_limit], many=True).data
        columns[col] = {"tasks": tasks_data, "total_count": total}
    return Response({"columns": columns})


def _kanban_load_more(base_qs, status_param, query_params):
    valid_columns = {col for col in KANBAN_COLUMNS}
    if status_param not in valid_columns:
        return Response(
            {"detail": f"Invalid status '{status_param}'. Valid values: {sorted(valid_columns)}"},
            status=400,
        )
    offset = max(int(query_params.get("offset", 0)), 0)
    limit = min(int(query_params.get("limit", 20)), 200)
    col_qs = base_qs.filter(status__in=get_statuses_for_column(status_param))
    total = col_qs.count()
    col_qs = order_column_queryset(col_qs, status_param)
    tasks_data = TaskKanbanCardSerializer(col_qs[offset:offset + limit], many=True).data
    return Response({
        "tasks": tasks_data,
        "total_count": total,
        "has_more": (offset + limit) < total,
    })


@api_view(["GET"])
def task_search(request):
    query = (request.query_params.get("q") or "").strip()
    if not query:
        raise ValidationError({"q": "This query parameter is required."})

    scope = (request.query_params.get("scope") or "board").strip().lower()
    if scope not in {"board", "global"}:
        raise ValidationError({"scope": "Must be one of: board, global."})

    board_id = request.query_params.get("board_id") or request.query_params.get("board")
    if scope == "board" and not board_id:
        raise ValidationError({"board_id": "This query parameter is required for board scope."})

    limit = _coerce_limit(request.query_params.get("limit"), default=10)
    limit = max(1, min(limit, 10))

    qs = Task.objects.select_related("board", "spec")
    if scope == "board":
        qs = qs.filter(board_id=board_id)

    qs = qs.filter(Q(title__icontains=query) | Q(spec__title__icontains=query))
    qs = qs.annotate(
        search_rank=Case(
            When(title__istartswith=query, then=Value(0)),
            When(spec__title__istartswith=query, then=Value(1)),
            When(title__icontains=query, then=Value(2)),
            When(spec__title__icontains=query, then=Value(3)),
            default=Value(4),
            output_field=IntegerField(),
        )
    ).order_by("search_rank", "-last_updated_at", "id")[:limit]

    payload = [
        {
            "task_id": task.id,
            "title": task.title,
            "status": task.status,
            "board_id": task.board_id,
            "board_name": task.board.name,
            "spec_id": task.spec_id,
            "spec_title": task.spec.title if task.spec_id else None,
        }
        for task in qs
    ]
    return Response({"results": TaskSearchResultSerializer(payload, many=True).data})


@api_view(["GET"])
def runtime_process_monitor(request):
    """Show running Odin processes joined with Taskit task context."""
    from .integrations.odin_runtime import fetch_odin_status

    board_id = request.query_params.get("board_id") or request.query_params.get("board")
    spec_id = request.query_params.get("spec_id") or request.query_params.get("spec")
    running_only = str(request.query_params.get("running_only", "true")).lower() not in ("0", "false", "no")

    status_result = fetch_odin_status()
    if not status_result.get("ok"):
        logger.warning(
            "runtime_process_monitor: odin status unavailable (%s); returning Taskit-only fallback",
            status_result.get("error", "unknown error"),
        )

    odin_tasks = status_result.get("tasks", [])
    odin_by_id = {}
    for row in odin_tasks:
        if not isinstance(row, dict):
            continue
        task_id = str(row.get("id") or "").strip()
        if task_id:
            odin_by_id[task_id] = row

    qs = Task.objects.select_related("assignee").all()
    if board_id:
        qs = qs.filter(board_id=board_id)
    if spec_id:
        qs = qs.filter(spec_id=spec_id)
    if running_only:
        qs = qs.filter(status__in=[TaskStatus.IN_PROGRESS, TaskStatus.EXECUTING])

    rows = []
    for task in qs.order_by("-last_updated_at"):
        odin_row = odin_by_id.get(str(task.id), {})
        odin_status = str(odin_row.get("status", "")).upper() if isinstance(odin_row, dict) else ""
        if running_only and odin_status and odin_status not in ("IN_PROGRESS", "EXECUTING"):
            continue
        rows.append(
            {
                "task_id": task.id,
                "title": task.title,
                "status": task.status,
                "odin_status": odin_status or task.status,
                "board_id": task.board_id,
                "spec_id": task.spec_id,
                "assignee": task.assignee.name if task.assignee else None,
                "agent": odin_row.get("agent") if isinstance(odin_row, dict) else None,
                "model": odin_row.get("model") if isinstance(odin_row, dict) else None,
                "elapsed": odin_row.get("elapsed") if isinstance(odin_row, dict) else None,
                "updated_at": task.last_updated_at.isoformat(),
            }
        )

    return Response(
        {
            "tasks": rows,
            "summary": status_result.get("summary", {}),
            "source": "odin_status" if status_result.get("ok") else "taskit_fallback",
            "odin_available": bool(status_result.get("ok")),
            "odin_error": status_result.get("error", "") if not status_result.get("ok") else "",
            "odin_warning": status_result.get("warning", ""),
            "fetched_at": timezone.now().isoformat(),
        }
    )


@api_view(["GET"])
def runtime_odin_status(request):
    """Run plain `odin status` and return raw + parsed output for UI mirroring."""
    from .integrations.odin_runtime import run_odin_status

    spec = request.query_params.get("spec")
    agent = request.query_params.get("agent")
    status_filter = request.query_params.get("status")

    result = run_odin_status(spec=spec, agent=agent, status=status_filter)
    http_status = status.HTTP_200_OK if result.get("ok") else status.HTTP_502_BAD_GATEWAY
    return Response(
        {
            "ok": bool(result.get("ok")),
            "command": result.get("command", []),
            "exit_code": result.get("exit_code"),
            "raw_stdout": result.get("stdout", ""),
            "raw_stderr": result.get("stderr", ""),
            "rows": result.get("rows", []),
            "summary": result.get("summary", {}),
            "total": result.get("total", 0),
            "parse_ok": bool(result.get("parse_ok", False)),
            "parse_warnings": result.get("parse_warnings", []),
            "error": result.get("error", ""),
            "fetched_at": timezone.now().isoformat(),
        },
        status=http_status,
    )


@api_view(["GET"])
def runtime_forced_provider(request):
    """Expose the current forced provider configuration for read-only UI use."""
    return Response(_forced_provider_response())


def _load_provider_usage():
    """Assemble provider usage entries for the providers page.

    Returns ``(entries, error)`` where ``error`` is ``None`` on success. This is
    the network boundary; split out so tests can patch the seam. Mirrors the
    odin graceful-degradation convention (orchestrator._fetch_quota): it never
    raises — any failure (package missing, config error, provider fetch error)
    yields an honest empty list plus a human-readable error string, so the page
    renders an "unavailable" state instead of a 500.
    """
    try:
        from harness_usage_status.config import load_config
        from harness_usage_status.providers.registry import get_all_providers
    except ImportError:
        return [], "harness_usage_status package not installed"

    try:
        config = load_config()
        providers = get_all_providers(config.get_provider_configs())
        provider_list = list(providers.values())

        async def fetch_all():
            usage_tasks = [p.get_usage() for p in provider_list]
            status_tasks = [p.get_status() for p in provider_list]
            usages = await asyncio.gather(*usage_tasks, return_exceptions=True)
            statuses = await asyncio.gather(*status_tasks, return_exceptions=True)
            return usages, statuses

        usages, statuses = asyncio.run(fetch_all())

        result = []
        for i, provider in enumerate(provider_list):
            usage = usages[i]
            provider_status = statuses[i]

            entry = {"name": provider.name}

            if isinstance(provider_status, Exception):
                entry.update({
                    "state": "unknown",
                    "latency_ms": None,
                    "last_checked": None,
                    "message": str(provider_status),
                })
            else:
                entry.update({
                    "state": provider_status.state.value,
                    "latency_ms": provider_status.latency_ms,
                    "last_checked": provider_status.last_checked.isoformat() if provider_status.last_checked else None,
                    "message": provider_status.message,
                })

            if isinstance(usage, Exception):
                entry.update({
                    "plan": None,
                    "quota_limit": None,
                    "used": None,
                    "remaining": None,
                    "usage_pct": None,
                    "unit": "unknown",
                    "reset_date": None,
                    "raw": None,
                })
            else:
                entry.update({
                    "plan": usage.plan,
                    "quota_limit": usage.quota_limit,
                    "used": usage.used,
                    "remaining": usage.remaining,
                    "usage_pct": usage.usage_pct,
                    "unit": usage.unit,
                    "reset_date": usage.reset_date.isoformat() if usage.reset_date else None,
                    "raw": usage.raw,
                })

            result.append(entry)
        return result, None
    except Exception as e:
        logger.warning("Provider usage unavailable: %s", e, exc_info=True)
        return [], f"provider usage unavailable: {e}"


@api_view(["GET"])
def runtime_provider_usage(request):
    """Return AI provider usage quotas and health status.

    Always returns HTTP 200. When usage data is unavailable (package missing,
    misconfigured, or a provider fetch error) the payload carries an honest
    ``error`` string and an empty ``providers`` list, so the page renders an
    "unavailable" state rather than a 500.
    """
    entries, error = _load_provider_usage()
    return Response({
        "providers": entries,
        "error": error,
        "fetched_at": timezone.now().isoformat(),
    })


def _list_child_directories(path_value, limit, include_hidden=False):
    path = Path(path_value)
    if not path.exists():
        raise FileNotFoundError(f"Path not found: {path_value}")
    if not path.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {path_value}")

    entries = []
    for child in path.iterdir():
        if not child.is_dir():
            continue
        if not include_hidden and child.name.startswith("."):
            continue
        has_children = False
        try:
            has_children = any(grand_child.is_dir() for grand_child in child.iterdir())
        except Exception:
            has_children = False
        entries.append(
            {
                "name": child.name,
                "path": str(child.resolve()),
                "has_children": has_children,
            }
        )

    entries.sort(key=lambda item: item["name"].lower())
    return entries[:limit]


def _coerce_limit(raw_value, default=25, max_value=100):
    try:
        parsed = int(raw_value or default)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, max_value))


def _normalize_abs_path(raw_value):
    value = str(raw_value or "").strip()
    if not value:
        return ""
    expanded = os.path.expanduser(value)
    if not os.path.isabs(expanded):
        return ""
    return str(Path(expanded))


@api_view(["GET"])
def runtime_directories_suggest(request):
    """Suggest directories for a partially typed absolute path."""
    query = _normalize_abs_path(request.query_params.get("q"))
    limit = _coerce_limit(request.query_params.get("limit"), default=20)

    if not query:
        return Response({"base_path": "", "entries": []})

    target = Path(query)
    if target.exists() and target.is_dir():
        base = target
        prefix = ""
    else:
        base = target.parent if str(target.parent) else Path("/")
        prefix = target.name.lower()

    if not base.exists() or not base.is_dir():
        return Response({"base_path": str(base), "entries": []})

    try:
        include_hidden = prefix.startswith(".")
        # When filtering by prefix, fetch ALL children first so alphabetically
        # late entries (like "video-*") aren't truncated before the filter runs.
        fetch_limit = 500 if prefix else limit
        entries = _list_child_directories(
            str(base),
            limit=fetch_limit,
            include_hidden=include_hidden,
        )
    except PermissionError:
        return Response(
            {"detail": f"Permission denied: {base}"},
            status=status.HTTP_403_FORBIDDEN,
        )
    except Exception as exc:
        return Response(
            {"detail": str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if prefix:
        entries = [entry for entry in entries if entry["name"].lower().startswith(prefix)]

    return Response({"base_path": str(base.resolve()), "entries": entries[:limit]})


@api_view(["GET"])
def runtime_directories_children(request):
    """List immediate child directories for a given absolute path."""
    raw_path = request.query_params.get("path")
    path_value = _normalize_abs_path(raw_path)
    limit = _coerce_limit(request.query_params.get("limit"), default=100)
    include_hidden = str(request.query_params.get("include_hidden", "false")).lower() in (
        "1",
        "true",
        "yes",
    )

    if not path_value:
        return Response(
            {"detail": "Query param 'path' must be an absolute path."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        entries = _list_child_directories(path_value, limit=limit, include_hidden=include_hidden)
    except PermissionError:
        return Response(
            {"detail": f"Permission denied: {path_value}"},
            status=status.HTTP_403_FORBIDDEN,
        )
    except (FileNotFoundError, NotADirectoryError) as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return Response({"base_path": str(Path(path_value).resolve()), "entries": entries})


@api_view(["POST"])
def runtime_stop(request):
    """Stop a running task via `odin stop`, then apply Taskit status transition."""
    ser = RuntimeStopSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    d = ser.validated_data

    task = get_object_or_404(Task, pk=d["task_id"])
    if task.status != TaskStatus.EXECUTING:
        return Response(
            {"detail": f"Task {task.id} is {task.status}, expected EXECUTING."},
            status=status.HTTP_409_CONFLICT,
        )

    body, http_status = _perform_stop_flow(
        task=task,
        target_status=d["target_status"],
        updated_by=d["updated_by"],
        reason=(d.get("reason") or "").strip() or "runtime_monitor_stop",
        force=d.get("force", False),
    )
    return Response(body, status=http_status)


# ── Presets ──────────────────────────────────────────────────────

@api_view(["GET"])
def sandbox_config(request):
    """Read-only sandbox configuration (visible-decisions rule): per-agent
    VM memory from the repo's .odin/config.yaml plus the backend budget
    settings. The Settings page renders this so RAM sizing is a decision a
    human can SEE, not a hidden constant."""
    import yaml
    from pathlib import Path
    from .sandbox_budget import default_vm_mem_mib, get_budget_mib
    cfg_path = Path(__file__).resolve().parents[3] / ".odin" / "config.yaml"
    agents = {}
    try:
        raw = yaml.safe_load(cfg_path.read_text()) or {}
        for name, acfg in (raw.get("agents") or {}).items():
            if isinstance(acfg, dict) and "microsandbox_mem_size_mib" in acfg:
                agents[name] = acfg["microsandbox_mem_size_mib"]
    except Exception:
        agents = {}
    return Response({
        "agents_vm_mem_mib": agents,
        "default_vm_mem_mib": default_vm_mem_mib(),
        "memory_budget_mib": get_budget_mib(),
        "source": str(cfg_path),
    })


@api_view(["GET"])
def list_presets(request):
    # load_presets validates any preset carrying an `audit` block against the
    # audit-preset contract (tasks/audit_presets.py) and enforces unique ids.
    data = load_presets()
    if request.query_params.get("include_disabled") not in ("1", "true", "True"):
        data = {**data, "presets": [p for p in data["presets"] if not p.get("disabled")]}
    return Response(data)


@api_view(["POST"])
def manage_preset(request):
    """The one write path for data/task_presets.json: add / update / delete
    / disable / enable. Every action is a literal edit to the JSON file on
    disk (no DB-backed shadow copy) so every preset change is a git diff.
    """
    from .audit_presets import PresetValidationError, save_presets, validate_preset_shape

    action = (request.data.get("action") or "").strip().lower()
    if action not in ("add", "update", "delete", "disable", "enable"):
        return Response({"error": f"invalid action {action!r}"}, status=400)

    try:
        data = load_presets()
    except PresetValidationError as exc:
        return Response({"error": f"existing presets file invalid: {exc}"}, status=500)

    presets = data["presets"]
    index_by_id = {p["id"]: i for i, p in enumerate(presets)}

    if action in ("add", "update"):
        preset = request.data.get("preset")
        if not isinstance(preset, dict):
            return Response({"error": "'preset' object required"}, status=400)
        pid = preset.get("id")
        if action == "add" and pid in index_by_id:
            return Response({"error": f"preset {pid!r} already exists"}, status=409)
        if action == "update" and pid not in index_by_id:
            return Response({"error": f"preset {pid!r} not found"}, status=404)
        try:
            validate_preset_shape(preset)
        except PresetValidationError as exc:
            return Response({"error": str(exc)}, status=400)
        if action == "add":
            preset.setdefault("sort_order", len(presets) + 1)
            presets.append(preset)
        else:
            presets[index_by_id[pid]] = preset
    else:
        pid = request.data.get("id")
        if pid not in index_by_id:
            return Response({"error": f"preset {pid!r} not found"}, status=404)
        if action == "delete":
            presets.pop(index_by_id[pid])
        elif action == "disable":
            presets[index_by_id[pid]]["disabled"] = True
        elif action == "enable":
            presets[index_by_id[pid]].pop("disabled", None)

    save_presets(data)
    return Response(load_presets())


# ── Executor Capacity ──────────────────────────────────────────────────────

@api_view(["GET"])
def executor_capacity(request):
    """Return running task count and max concurrency limit.

    Task #353: also surfaces the global memory-share accounting so the
    app header can render the truthful "3 executing + 1 review = 4/4
    memory shares" line. Same primitive the dispatcher gates on —
    `memory_share_summary()` reads `compute_reserved_mib`/`get_budget_mib`
    directly — so the badge and the gate can never drift.
    """
    try:
        setting = SystemSetting.objects.get(key="executor_max_concurrency")
        max_concurrency = setting.value
    except SystemSetting.DoesNotExist:
        max_concurrency = getattr(settings, "DAG_EXECUTOR_MAX_CONCURRENCY", 3)

    running_count = Task.objects.filter(status=TaskStatus.EXECUTING).count()

    # Get suggested max from a dummy SystemSetting instance
    dummy_setting = SystemSetting(key="executor_max_concurrency", value=max_concurrency)
    suggested_max = dummy_setting.get_suggested_max()

    from .sandbox_budget import memory_share_summary
    memory_shares = memory_share_summary()

    return Response({
        "running": running_count,
        "max": max_concurrency,
        "suggested_max": suggested_max,
        # Task #353: honest share split from the same accounting the
        # dispatcher uses. `executing` mirrors `running` for backwards
        # compat; the new fields expose the cross-kind breakdown so the
        # badge can render "3+1/4" instead of "3/4".
        "executing": memory_shares["executing_count"],
        "reflecting": memory_shares["reflecting_count"],
        "shares_in_use": memory_shares["shares_in_use"],
        "memory_budget_mib": memory_shares["budget_mib"],
        "memory_reserved_mib": memory_shares["reserved_mib"],
        "memory_max_shares": memory_shares["max_shares"],
        "memory_share_holders": memory_shares["holders"],
    })


@api_view(["GET", "POST"])
def executor_max_concurrency(request):
    """Get or set the maximum executor concurrency."""
    if request.method == "GET":
        try:
            setting = SystemSetting.objects.get(key="executor_max_concurrency")
            value = setting.value
        except SystemSetting.DoesNotExist:
            value = getattr(settings, "DAG_EXECUTOR_MAX_CONCURRENCY", 3)

        dummy_setting = SystemSetting(key="executor_max_concurrency", value=value)
        suggested_max = dummy_setting.get_suggested_max()

        return Response({
            "value": value,
            "suggested_max": suggested_max,
        })

    # POST: set max concurrency
    value = request.data.get("value")
    if value is None:
        return Response(
            {"error": "Missing required field: value"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        value = int(value)
    except (ValueError, TypeError):
        return Response(
            {"error": "value must be an integer"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if value < 1:
        return Response(
            {"error": "value must be at least 1"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    setting, created = _update_executor_max_concurrency(value)

    suggested_max = setting.get_suggested_max()

    return Response({
        "value": setting.value,
        "suggested_max": suggested_max,
    })


# ── W12.4 agent-league drilldown ────────────────────────────────────


@api_view(["GET"])
def agent_tasks(request, agent_name):
    """List every task an agent has touched, with board/spec/status filters.

    Used by the stats page's agent-league drilldown: clicking an
    agent row in the league table navigates here with the agent name,
    and the page renders a clickable task list (each row links to
    the task detail modal via ``?taskId=<id>``).

    Match strategy: the agent short name (``plan`` from
    ``plan+opus@odin.agent``, ``claude`` from ``claude@odin.agent``,
    or a User.name match) — the league table's bucket name. This is
    the same extraction the league and per-agent rollup already use,
    so the drilldown lands on the same population of tasks the
    operator just clicked.

    Query params:
      board_id — restrict to one board
      spec_id  — restrict to one spec
      status   — restrict to one status (e.g. DONE)
      limit    — max rows (default 50, hard cap 200)

    Response shape: ``{"tasks": [{"task_id", "title", "status",
    "board_id", "board_name", "spec_id", "spec_title", "agent_name",
    "model_name"}, ...], "meta": {"agent": ..., "count": ...}}``
    """
    from .agent_stats import _extract_agent_name

    agent_name = (agent_name or "").strip()
    if not agent_name:
        return Response(
            {"detail": "Agent name is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    board_id = request.query_params.get("board_id") or request.query_params.get("board")
    spec_id = request.query_params.get("spec_id") or request.query_params.get("spec")
    status_filter = request.query_params.get("status")
    try:
        limit = int(request.query_params.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 200))

    qs = Task.objects.select_related("board", "spec", "assignee")

    if board_id:
        try:
            qs = qs.filter(board_id=int(board_id))
        except (TypeError, ValueError):
            return Response(
                {"detail": "board_id must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    if spec_id:
        try:
            qs = qs.filter(spec_id=int(spec_id))
        except (TypeError, ValueError):
            return Response(
                {"detail": "spec_id must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    if status_filter:
        qs = qs.filter(status=status_filter.upper())

    # Match assignee.name OR assignee.email local-part OR created_by
    # email local-part — the operator might have created a task
    # outside the agent harness, and the created_by email still
    # carries the agent identity (e.g. plan+opus@odin.agent).
    candidates = list(qs.filter(
        Q(assignee__name__iexact=agent_name)
        | Q(assignee__email__istartswith=f"{agent_name}+")
        | Q(assignee__email__iexact=f"{agent_name}@odin.agent")
        | Q(created_by__istartswith=f"{agent_name}+")
        | Q(created_by__iexact=f"{agent_name}@odin.agent")
    ).order_by("-last_updated_at", "-id")[:limit])

    rows = []
    for task in candidates:
        # Resolve the agent short name from the assignee email first,
        # fall back to created_by. Same vocabulary the league uses so
        # the drilldown rows line up with the league bucket the
        # operator clicked.
        agent_email = (
            getattr(task.assignee, "email", None)
            if task.assignee_id else None
        ) or task.created_by
        resolved = _extract_agent_name(agent_email) if agent_email else "unknown"
        rows.append({
            "task_id": task.id,
            "title": task.title,
            "status": task.status,
            "board_id": task.board_id,
            "board_name": task.board.name if task.board_id else None,
            "spec_id": task.spec_id,
            "spec_title": task.spec.title if task.spec_id else None,
            "agent_name": resolved,
            "model_name": task.model_name or "",
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "last_updated_at": (
                task.last_updated_at.isoformat()
                if task.last_updated_at else None
            ),
        })

    return Response({
        "tasks": rows,
        "meta": {
            "agent": agent_name,
            "count": len(rows),
            "board_id": int(board_id) if board_id else None,
            "spec_id": int(spec_id) if spec_id else None,
            "status": status_filter.upper() if status_filter else None,
            "limit": limit,
        },
    })
