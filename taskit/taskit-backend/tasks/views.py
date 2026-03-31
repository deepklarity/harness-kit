import json
import os
import subprocess
from datetime import datetime, time
from pathlib import Path
from collections import deque

import yaml

from django.conf import settings
from django.db import transaction
from django.db.models import Case, Count, F, IntegerField, Q, Value, When
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from .kanban_ordering import move_task
from .models import (
    Board, BoardMembership, CommentAttachment, CommentType, Label,
    ReflectionReport, ReflectionStatus, ScheduleKind, ScheduleStatus, Spec, SpecComment, Task,
    TaskComment, TaskHistory, TaskSchedule, TaskScheduleRun, TaskStatus, User, UserSetting,
)
from .scheduling import (
    compute_schedule_next_run,
    create_schedule,
    maybe_finalize_schedule_run,
    parse_local_datetime,
    local_to_utc,
    rebind_local_datetime,
    ScheduleValidationError,
)
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
    CreateSpecSerializer,
    CreateTaskCommentSerializer,
    ModelToggleSerializer,
    PlanningResultSerializer,
    RoutingAgentSerializer,
    SpecCommentSerializer,
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
    SpecDiagnosticSerializer,
    SpecListSerializer,
    SpecSerializer,
    StopExecutionSerializer,
    TaskCommentSerializer,
    TaskDashboardSerializer,
    TaskDetailSerializer,
    TaskHistorySerializer,
    TaskScheduleSerializer,
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



def _reflection_reviewer_defaults(board=None):
    selection = get_forced_provider_selection()
    if selection.enabled:
        return selection.provider, selection.model
    if board and board.reflection_model:
        return "claude", board.reflection_model
    return "claude", "claude-sonnet-4-5-20250929"


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


def _clear_stop_guards(metadata):
    metadata.pop("ignore_execution_results", None)
    metadata.pop("stopped_run_token", None)
    metadata.pop("execution_stopped_at", None)


def _trigger_auto_reflection(task):
    """Create a ReflectionReport and dispatch the Celery task if no active reflection exists.

    Called when a task transitions to REVIEW — mirrors the pattern used for
    auto-execution on IN_PROGRESS (explicit call in the view, not a signal).
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

    reviewer_agent, reviewer_model = _reflection_reviewer_defaults(board=task.board)
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
    )

    from .dag_executor import execute_reflection
    execute_reflection.delay(report.id)

    logger.info("[task:%s] Auto-reflection triggered: report_id=%s", task.id, report.id)


def _merge_task_on_reflection_pass(task):
    """Dispatch merge of task branch into spec branch as a Celery task.

    Called when REVIEW → TESTING (pass) or REVIEW → FAILED (3 strikes).
    The actual merge runs in the Celery worker where odin is importable
    (the Django web process cannot import odin.worktree).
    """
    branch = (task.metadata or {}).get("branch")
    if not branch:
        return

    # Skip if already merged (e.g. manual merge or duplicate call)
    if (task.metadata or {}).get("merge_status") == "merged":
        return

    from .dag_executor import merge_task_on_reflection
    merge_task_on_reflection.delay(task.id)


# -- Quota keywords matched against failure_reason, verdict_summary, and quota_failure --
_QUOTA_KEYWORDS = [
    "quota", "rate limit", "rate_limit", "429", "too many requests",
    "usage limit", "out of quota", "quota exceeded", "quota_failure",
]


def _is_quota_failure(task, report):
    """Detect whether a task failure was caused by quota/rate-limit exhaustion.

    Checks three sources (in order):
    1. Reflection report's quota_failure field (most reliable — reviewer detected it)
    2. Task metadata last_failure_type == "llm_call_failure" + quota keywords in reason
    3. Verdict summary containing quota-related keywords
    """
    # 1. Reflection explicitly flagged quota failure
    quota_field = getattr(report, "quota_failure", "") or ""
    if quota_field.strip() and quota_field.strip().lower() != "none.":
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


def _find_alternative_agent(task):
    """Find an alternative AGENT user on the same board, different from current assignee.

    Returns (User, model_name) or (None, None) if no alternative is available.
    Selects from board members with role=AGENT, excluding the current assignee.
    Falls back to any AGENT user if no board-scoped alternatives exist.
    """
    from .models import BoardMembership, UserRole

    current_assignee_id = task.assignee_id
    board_id = task.board_id

    # Prefer agents that are members of the same board
    board_agent_ids = BoardMembership.objects.filter(
        board_id=board_id,
        user__role=UserRole.AGENT,
    ).exclude(
        user_id=current_assignee_id,
    ).values_list("user_id", flat=True)

    candidates = User.objects.filter(
        id__in=board_agent_ids, role=UserRole.AGENT,
    )

    if not candidates.exists():
        # Fallback: any agent user not the current one
        candidates = User.objects.filter(role=UserRole.AGENT).exclude(
            id=current_assignee_id,
        )

    if not candidates.exists():
        return None, None

    # Pick the first available agent; prefer those with available_models set
    agent = candidates.order_by("id").first()

    # Determine model: use the agent's default model from available_models,
    # or derive from the agent name convention
    model_name = None
    available = agent.available_models or []
    if available:
        # Pick the first model (default) from the agent's available_models
        if isinstance(available[0], dict):
            model_name = available[0].get("name")
        elif isinstance(available[0], str):
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
    """If the task failed due to quota exhaustion, reassign to a different agent.

    Mutates task in place (assignee, model_name) and records history + comment.
    Does NOT save the task — the caller saves it when setting status to IN_PROGRESS.
    """
    if not _is_quota_failure(task, report):
        return

    task.refresh_from_db(fields=["assignee_id", "model_name", "metadata"])
    old_assignee = task.assignee
    old_model = task.model_name

    new_agent, new_model = _find_alternative_agent(task)
    if new_agent is None:
        logger.warning(
            "[task:%s] Quota failure detected but no alternative agent available",
            task.id,
        )
        TaskComment.objects.create(
            task=task,
            author_email="system@taskit",
            author_label="system",
            content=(
                f"Quota/rate-limit failure detected for {old_assignee.name if old_assignee else 'unknown'} "
                f"({old_model or 'unknown model'}), but no alternative agent is available for reassignment."
            ),
            comment_type=CommentType.STATUS_UPDATE,
        )
        return

    old_assignee_name = old_assignee.name if old_assignee else "unassigned"

    # Update task fields
    task.assignee = new_agent
    if new_model:
        task.model_name = new_model
    task.save(update_fields=["assignee_id", "model_name"])

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

    # Post explanatory comment
    TaskComment.objects.create(
        task=task,
        author_email="system@taskit",
        author_label="system",
        content=(
            f"Quota/rate-limit failure detected for {old_assignee_name} ({old_model or 'unknown model'}). "
            f"Reassigned to {new_agent.name} ({new_model or 'default model'}) for retry."
        ),
        comment_type=CommentType.STATUS_UPDATE,
    )

    logger.info(
        "[task:%s] Quota failure reassignment: %s/%s → %s/%s",
        task.id, old_assignee_name, old_model, new_agent.name, new_model,
    )


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
    settings_obj.save(update_fields=["preferred_ide_id", "updated_at"])
    return Response(UserSettingSerializer(settings_obj).data)


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


def _default_model_for_user(user):
    if not user:
        return None
    models = [m for m in (user.available_models or []) if isinstance(m, dict) and m.get("name")]
    if not models:
        return None
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


def _exclude_hidden_scheduled_tasks(qs):
    return qs.exclude(
        schedule_id__isnull=False,
        schedule__status__in=[ScheduleStatus.ACTIVE, ScheduleStatus.PAUSED],
        status__in=[TaskStatus.BACKLOG, TaskStatus.TODO, TaskStatus.REVIEW, TaskStatus.TESTING, TaskStatus.DONE, TaskStatus.FAILED],
    )


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
        """Create BoardMembership records for all agent Users, skipping disabled ones."""
        disabled_set = set(disabled_agents or [])
        agent_users = User.objects.filter(role="AGENT")

        new_memberships = []
        for user in agent_users:
            if user.name in disabled_set:
                continue
            new_memberships.append(BoardMembership(board=board, user=user))

        if new_memberships:
            BoardMembership.objects.bulk_create(new_memberships, ignore_conflicts=True)

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

        all_agents = User.objects.filter(role="AGENT")
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
        agent_user = User.objects.filter(email=email, role="AGENT").first()
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

        return Response({"agents": RoutingAgentSerializer(agents, many=True).data})

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


def _attempt_odin_stop(task, force=False):
    """Try odin CLI stop first; fall back to execution strategy stop when needed."""
    from .integrations.odin_runtime import stop_with_odin
    stop_result = stop_with_odin(task.id, force=force)
    if stop_result.get("ok"):
        return stop_result

    from .execution import get_strategy
    strategy = get_strategy()
    if not strategy:
        return stop_result

    fallback = strategy.stop(task, force=force)
    if fallback.get("ok"):
        fallback["engine"] = f"{fallback.get('engine', 'strategy')} (fallback)"
        fallback["fallback_used"] = True
        fallback["odin_error"] = stop_result.get("error", "")
        return fallback

    return {
        "ok": False,
        "engine": "odin_cli+strategy",
        "error": stop_result.get("error") or fallback.get("error") or "Failed to stop execution.",
        "odin_stop": stop_result,
        "strategy_stop": fallback,
    }


def _apply_stop_transition(task, target_status, updated_by, reason, stop_result):
    """Persist post-stop status/metadata/history/comment updates."""
    run_token = ((task.metadata or {}).get("active_execution") or {}).get("run_token")
    metadata = dict(task.metadata or {})
    metadata.pop("active_execution", None)
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


class TaskViewSet(viewsets.ModelViewSet):
    serializer_class = TaskSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        query_params = self.request.query_params
        qs = (
            Task.objects.select_related("assignee", "spec")
            .prefetch_related("labels")
            .annotate(comment_count=Count("comments"))
        )
        qs = _exclude_hidden_scheduled_tasks(qs)

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
                    _ensure_board_membership(task.board, User.objects.get(pk=d["assignee_id"]))

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

        if "skip_reflection" in d:
            if _record_change(histories, task, "skip_reflection", task.skip_reflection, d["skip_reflection"], updated_by):
                task.skip_reflection = d["skip_reflection"]

        # A new queue/run cycle clears stale stop guards from prior executions.
        if "status" in d and d["status"] == TaskStatus.IN_PROGRESS and old_status != TaskStatus.IN_PROGRESS:
            task.metadata = dict(task.metadata or {})
            _clear_stop_guards(task.metadata)

        task.save()
        TaskHistory.objects.bulk_create(histories)

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

        stop_result = _attempt_odin_stop(task)
        if not stop_result.get("ok"):
            return Response(
                {"detail": stop_result.get("error", "Failed to stop execution."), "stop": stop_result},
                status=status.HTTP_409_CONFLICT,
            )

        task.refresh_from_db()
        if task.status != TaskStatus.EXECUTING:
            return Response(
                {"detail": f"Task status changed to {task.status}; refusing post-stop move.", "stop": stop_result},
                status=status.HTTP_409_CONFLICT,
            )

        target_status = d["target_status"]
        updated_by = d["updated_by"]
        reason = (d.get("reason") or "").strip() or "user_drag_stop_confirm"
        payload = _apply_stop_transition(task, target_status, updated_by, reason, stop_result)
        return Response(payload)

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        task = get_object_or_404(Task, pk=pk)
        if task.status == TaskStatus.EXECUTING:
            return _executing_lock_response(task, ["assignee"])
        ser = AssignTaskSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        assignee = get_object_or_404(User, pk=ser.validated_data["assignee_id"])
        _validate_forced_task_target(assignee=assignee, model_name=task.model_name)
        old_assignee = str(task.assignee_id) if task.assignee_id else ""

        if old_assignee != str(assignee.id):
            TaskHistory.objects.create(
                task=task, schedule_run=task.current_schedule_run, field_name="assignee_id",
                old_value=old_assignee, new_value=str(assignee.id),
                changed_by=ser.validated_data["updated_by"],
            )

        task.assignee = assignee
        task.save()

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

        if old_assignee:
            TaskHistory.objects.create(
                task=task, schedule_run=task.current_schedule_run, field_name="assignee_id",
                old_value=old_assignee, new_value="",
                changed_by=ser.validated_data["updated_by"],
            )

        task.assignee = None
        task.save()

        return Response(_task_response(task.id))

    @action(detail=True, methods=["post"], url_path="labels", url_name="add-labels")
    def add_labels(self, request, pk=None):
        task = get_object_or_404(Task, pk=pk)
        ser = AddLabelsSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        old_labels = json.dumps(sorted(task.labels.values_list("id", flat=True)))
        labels = Label.objects.filter(id__in=ser.validated_data["label_ids"])
        task.labels.add(*labels)
        new_labels = json.dumps(sorted(task.labels.values_list("id", flat=True)))

        if old_labels != new_labels:
            TaskHistory.objects.create(
                task=task, schedule_run=task.current_schedule_run, field_name="labels",
                old_value=old_labels, new_value=new_labels,
                changed_by=ser.validated_data["updated_by"],
            )

        return Response(_task_response(task.id))

    @action(detail=True, methods=["delete"], url_path="labels", url_name="remove-labels")
    def remove_labels(self, request, pk=None):
        task = get_object_or_404(Task, pk=pk)
        ser = AddLabelsSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        old_labels = json.dumps(sorted(task.labels.values_list("id", flat=True)))
        labels = Label.objects.filter(id__in=ser.validated_data["label_ids"])
        task.labels.remove(*labels)
        new_labels = json.dumps(sorted(task.labels.values_list("id", flat=True)))

        if old_labels != new_labels:
            TaskHistory.objects.create(
                task=task, schedule_run=task.current_schedule_run, field_name="labels",
                old_value=old_labels, new_value=new_labels,
                changed_by=ser.validated_data["updated_by"],
            )

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
        comment = TaskComment.objects.create(task=task, schedule_run=task.current_schedule_run, **data)
        # Link uploaded file attachments (screenshots) to this comment
        if attachment_ids:
            CommentAttachment.objects.filter(
                id__in=attachment_ids, task=task, comment__isnull=True,
            ).update(comment=comment)
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

        # Clear pending question flag
        task.metadata = task.metadata or {}
        task.metadata.pop("has_pending_question", None)
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

        stopped_run_token = (task.metadata or {}).get("stopped_run_token")
        incoming_run_token = (exec_meta.get("taskit_run_token") or "").strip()
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
            task.kanban_position = move_task(task, target_status=new_status, target_index=None)
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
        if not success:
            if failure_type:
                task_metadata["last_failure_type"] = failure_type
            if failure_reason:
                task_metadata["last_failure_reason"] = failure_reason[:FAILURE_REASON_LIMIT]
            elif exec_result.get("error"):
                task_metadata["last_failure_reason"] = str(exec_result.get("error"))[:FAILURE_REASON_LIMIT]
            if failure_origin:
                task_metadata["last_failure_origin"] = failure_origin[:FAILURE_ORIGIN_LIMIT]
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
        task.metadata = task_metadata

        task.save()
        TaskHistory.objects.bulk_create(histories)

        # 6. Create comment
        TaskComment.objects.create(
            task=task,
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
        forced = get_forced_provider_selection()
        reviewer_agent = forced.provider if forced.enabled else ser.validated_data["reviewer_agent"]
        reviewer_model = forced.model if forced.enabled else ser.validated_data["reviewer_model"]

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

        # Post a reflection summary comment on the task when completed
        if new_status == "COMPLETED" and report.verdict_summary:
            verdict_label = (report.verdict or "").upper()
            comment_content = f"**Reflection: {verdict_label}**\n\n{report.verdict_summary}"
            TaskComment.objects.create(
                task=report.task,
                schedule_run=report.task.current_schedule_run,
                author_email=report.requested_by or "system@odin.agent",
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

        # Auto-advance: NEEDS_WORK or FAIL verdict retries or fails after 3 attempts
        verdict = (report.verdict or "").upper()
        if (
            new_status == "COMPLETED"
            and verdict in ("NEEDS_WORK", "FAIL")
        ):
            task = report.task
            task.refresh_from_db(fields=["status"])
            if task.status == TaskStatus.REVIEW:
                completed_count = ReflectionReport.objects.filter(
                    task=task, status=ReflectionStatus.COMPLETED,
                ).count()

                if completed_count >= 3:
                    # 3 strikes — fail the task (no merge; branch preserved for inspection)
                    old_status = task.status
                    task.status = TaskStatus.FAILED
                    task.save(update_fields=["status"])
                    TaskHistory.objects.create(
                        task=task,
                        schedule_run=task.current_schedule_run,
                        field_name="status",
                        old_value=old_status,
                        new_value=TaskStatus.FAILED,
                        changed_by="system@taskit",
                    )
                    TaskComment.objects.create(
                        task=task,
                        schedule_run=task.current_schedule_run,
                        author_email="system@taskit",
                        author_label="system",
                        content="Task failed after 3 reflection attempts without passing.",
                        comment_type=CommentType.STATUS_UPDATE,
                    )
                    maybe_finalize_schedule_run(task, TaskStatus.FAILED)
                    logger.info(
                        "Task %s FAILED after %d reflection attempts without passing",
                        task.id, completed_count,
                    )
                else:
                    # Check if this failure was quota/rate-limit related
                    # and reassign to a different agent if so
                    _maybe_reassign_on_quota_failure(task, report)

                    # Send back for another execution attempt
                    old_status = task.status
                    task.status = TaskStatus.IN_PROGRESS
                    task.save(update_fields=["status"])
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
                    # Fire execution strategy (mirrors TaskViewSet.update lines 746-756)
                    if task.assignee_id:
                        from .execution import get_strategy
                        strategy = get_strategy()
                        if strategy:
                            logger.info("Firing execution strategy for task %s after %s retry", task.id, verdict)
                            strategy.trigger(task)

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
        return qs

    def get_serializer_class(self):
        if self.action == "create":
            return CreateSpecSerializer
        if self.action == "list":
            return SpecListSerializer
        return SpecSerializer

    def create(self, request, *args, **kwargs):
        serializer = CreateSpecSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        spec = serializer.save()
        return Response(
            SpecSerializer(spec).data, status=status.HTTP_201_CREATED
        )

    def retrieve(self, request, *args, **kwargs):
        spec = get_object_or_404(
            Spec.objects.prefetch_related("tasks__assignee", "tasks__labels", "comments"),
            pk=kwargs["pk"],
        )
        return Response(SpecSerializer(spec).data)

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

    @action(detail=True, methods=["post"])
    def clone(self, request, pk=None):
        from django.db import transaction

        # Keys in metadata that are tied to a specific execution/worktree
        # and must NOT carry over to the clone.
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
                    if k not in TASK_METADATA_STRIP_KEYS
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

    @action(detail=True, methods=["get"], url_path="comments")
    def comments(self, request, pk=None):
        """List comments for a spec."""
        spec = get_object_or_404(Spec, pk=pk)
        qs = SpecComment.objects.filter(spec=spec)
        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(
                SpecCommentSerializer(page, many=True).data
            )
        return Response(SpecCommentSerializer(qs, many=True).data)

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
        .prefetch_related("labels", "history")
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
    """Kanban payload returns all cards for a board without pagination/filter/sort controls."""
    board_id = request.query_params.get("board_id") or request.query_params.get("board")
    query_params = request.query_params
    qs = (
        Task.objects.select_related("assignee")
        .prefetch_related("labels")
        .annotate(comment_count=Count("comments"))
        .order_by("kanban_position", "id")
    )
    qs = _exclude_hidden_scheduled_tasks(qs)
    if board_id:
        qs = qs.filter(board_id=board_id)
    qs = _apply_date_range(qs, query_params, "created_at", "date_from", "date_to")
    return Response(TaskListSerializer(qs, many=True).data)


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

    stop_result = _attempt_odin_stop(task, force=d.get("force", False))
    if not stop_result.get("ok"):
        return Response(
            {"detail": stop_result.get("error", "Failed to stop execution."), "stop": stop_result},
            status=status.HTTP_409_CONFLICT,
        )

    task.refresh_from_db()
    if task.status != TaskStatus.EXECUTING:
        return Response(
            {"detail": f"Task status changed to {task.status}; refusing post-stop move.", "stop": stop_result},
            status=status.HTTP_409_CONFLICT,
        )

    payload = _apply_stop_transition(
        task=task,
        target_status=d["target_status"],
        updated_by=d["updated_by"],
        reason=(d.get("reason") or "").strip() or "runtime_monitor_stop",
        stop_result=stop_result,
    )
    return Response(payload)


# ── Presets ──────────────────────────────────────────────────────

@api_view(["GET"])
def list_presets(request):
    filepath = Path(__file__).resolve().parent.parent / "data" / "task_presets.json"
    with open(filepath) as f:
        data = json.load(f)
    return Response(data)
