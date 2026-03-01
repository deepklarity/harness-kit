import json
import os
import subprocess
from datetime import datetime, time
from pathlib import Path

import yaml

from django.conf import settings
from django.db.models import Count, F, Q
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
    ReflectionReport, ReflectionStatus, Spec, SpecComment, Task, TaskComment,
    TaskHistory, TaskStatus, User,
)
from .utils.logger import logger
from .permissions import IsAdmin
from .serializers import (
    AddLabelsSerializer,
    AssignTaskSerializer,
    BoardDetailSerializer,
    BoardListSerializer,
    BoardMemberIdsSerializer,
    BoardSerializer,
    CreateBoardSerializer,
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
    RuntimeStopSerializer,
    SpecDiagnosticSerializer,
    SpecListSerializer,
    SpecSerializer,
    StopExecutionSerializer,
    TaskCommentSerializer,
    TaskDashboardSerializer,
    TaskDetailSerializer,
    TaskHistorySerializer,
    TaskListSerializer,
    TaskSerializer,
    TaskWithHistorySerializer,
    UnassignTaskSerializer,
    UpdateTaskSerializer,
    UserSerializer,
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


def _clear_stop_guards(metadata):
    metadata.pop("ignore_execution_results", None)
    metadata.pop("stopped_run_token", None)
    metadata.pop("execution_stopped_at", None)


def _trigger_auto_reflection(task):
    """Create a ReflectionReport and dispatch the Celery task if no active reflection exists.

    Called when a task transitions to REVIEW — mirrors the pattern used for
    auto-execution on IN_PROGRESS (explicit call in the view, not a signal).
    """
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

    report = ReflectionReport.objects.create(
        task=task,
        reviewer_agent="claude",
        reviewer_model="claude-sonnet-4-5-20250929",
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
            task=task, field_name=field_name,
            old_value=old_str, new_value=new_str,
            changed_by=changed_by,
        ))
        return True
    return False


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
            member_count=Count("memberships", distinct=True)
        )
        query_params = self.request.query_params

        search = query_params.get("search")
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(description__icontains=search))

        qs = _apply_date_range(qs, query_params, "created_at", "created_from", "created_to")
        qs = _apply_date_range(qs, query_params, "updated_at", "updated_from", "updated_to")

        tokens = _parse_sort_tokens(
            query_params.get("sort"),
            {"name", "created_at", "updated_at", "member_count"},
            default_tokens=[("created_at", True)],
        )
        qs = qs.order_by(*_build_order_by(tokens, {
            "name": "name",
            "created_at": "created_at",
            "updated_at": "updated_at",
            "member_count": "member_count",
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

        working_dir = ser.validated_data.pop("working_dir", None)
        auto_init = ser.validated_data.pop("auto_init", True)
        disabled_agents = ser.validated_data.pop("disabled_agents", [])

        if working_dir:
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

    def _validate_working_dir(self, path_str, exclude_board_id=None):
        """Validate a working directory path."""
        try:
            resolved = Path(path_str).resolve()
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
        path = request.query_params.get("path", "").strip()
        if not path:
            return Response({"error": "path query param required"}, status=status.HTTP_400_BAD_REQUEST)

        result = {
            "odin_exists": False,
            "linked_board": None,
            "can_init": False,
            "message": "",
        }

        try:
            resolved = Path(path).resolve()
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
            models_dict = {}
            for m in agent_user.available_models:
                if isinstance(m, dict):
                    models_dict[m.get("name", "")] = m.get("description", "")
            agents.append({
                "name": agent_user.name,
                "enabled": agent_user.id in member_user_ids,
                "cli_command": agent_user.cli_command,
                "capabilities": agent_user.capabilities or [],
                "cost_tier": agent_user.cost_tier or "medium",
                "default_model": agent_user.default_model,
                "premium_model": agent_user.premium_model,
                "models": models_dict,
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


class LabelViewSet(viewsets.ModelViewSet):
    queryset = Label.objects.all().order_by("id")
    serializer_class = LabelSerializer
    pagination_class = StandardPagination

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
        field_name="status",
        old_value=TaskStatus.EXECUTING,
        new_value=target_status,
        changed_by=updated_by,
    )

    TaskComment.objects.create(
        task=task,
        author_email=updated_by,
        author_label="taskit-ui",
        content=f"Execution stopped by user. Status changed to {target_status}.",
        comment_type=CommentType.STATUS_UPDATE,
    )

    return {"task": _task_response(task.id), "stop": stop_result}


class TaskViewSet(viewsets.ModelViewSet):
    serializer_class = TaskSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        query_params = self.request.query_params
        qs = (
            Task.objects.select_related("assignee")
            .prefetch_related("labels")
            .annotate(comment_count=Count("comments"))
        )

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
            depends_on=d.get("depends_on", []),
            complexity=d.get("complexity"),
            metadata=d.get("metadata", {}),
            model_name=model_name,
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
            old_json = json.dumps(task.depends_on)
            new_json = json.dumps(d["depends_on"])
            if _record_change(histories, task, "depends_on", old_json, new_json, updated_by):
                task.depends_on = d["depends_on"]

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

        # Keep metadata["selected_model"] in sync with model_name so odin
        # picks up UI-driven model changes at execution time.
        if "model_name" in d and d["model_name"]:
            task.metadata = dict(task.metadata or {})
            task.metadata["selected_model"] = d["model_name"]

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
        old_assignee = str(task.assignee_id) if task.assignee_id else ""

        if old_assignee != str(assignee.id):
            TaskHistory.objects.create(
                task=task, field_name="assignee_id",
                old_value=old_assignee, new_value=str(assignee.id),
                changed_by=ser.validated_data["updated_by"],
            )

        task.assignee = assignee
        task.save()

        # Auto-add assignee to board
        _ensure_board_membership(task.board, assignee)

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
                task=task, field_name="assignee_id",
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
                task=task, field_name="labels",
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
                task=task, field_name="labels",
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
        comment = TaskComment.objects.create(task=task, **data)
        # Link uploaded file attachments (screenshots) to this comment
        if attachment_ids:
            CommentAttachment.objects.filter(
                id__in=attachment_ids, task=task, comment__isnull=True,
            ).update(comment=comment)
        logger.info(
            "Comment on task %s by %s: %s",
            task.id, comment.author_label or comment.author_email, comment.content[:100],
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
            author_email=ser.validated_data["author_email"],
            author_label=ser.validated_data.get("author_label", ""),
            content=ser.validated_data["content"],
            attachments=[{"type": "question", "status": "pending"}],
            comment_type=CommentType.QUESTION,
        )

        task.metadata = task.metadata or {}
        task.metadata["has_pending_question"] = True
        task.save(update_fields=["metadata"])

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
        return Response(TaskDetailSerializer(task, context={"request": request}).data)

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

        report = ReflectionReport.objects.create(
            task=task,
            reviewer_agent=ser.validated_data["reviewer_agent"],
            reviewer_model=ser.validated_data["reviewer_model"],
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

        # Auto-advance: PASS verdict moves task from REVIEW → TESTING
        if (
            new_status == "COMPLETED"
            and report.verdict
            and report.verdict.upper() == "PASS"
        ):
            task = report.task
            # Re-read from DB to guard against concurrent status changes
            task.refresh_from_db(fields=["status"])
            if task.status == TaskStatus.REVIEW:
                old_status = task.status
                task.status = TaskStatus.TESTING
                task.save(update_fields=["status"])
                TaskHistory.objects.create(
                    task=task,
                    field_name="status",
                    old_value=old_status,
                    new_value=TaskStatus.TESTING,
                    changed_by="system@taskit",
                )
                logger.info(
                    "Auto-advanced task %s from REVIEW → TESTING after reflection PASS",
                    task.id,
                )

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
                    # 3 strikes — fail the task
                    old_status = task.status
                    task.status = TaskStatus.FAILED
                    task.save(update_fields=["status"])
                    TaskHistory.objects.create(
                        task=task,
                        field_name="status",
                        old_value=old_status,
                        new_value=TaskStatus.FAILED,
                        changed_by="system@taskit",
                    )
                    TaskComment.objects.create(
                        task=task,
                        author_email="system@taskit",
                        author_label="system",
                        content="Task failed after 3 reflection attempts without passing.",
                        comment_type=CommentType.STATUS_UPDATE,
                    )
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
        abandoned = request.data.get("abandoned")
        metadata = request.data.get("metadata")

        if title is not None:
            spec.title = title
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

        with transaction.atomic():
            spec = get_object_or_404(Spec, pk=pk)

            # 1. Clone the Spec
            import time
            timestamp = int(time.time())
            new_odin_id = f"{spec.odin_id}_copy_{timestamp}"

            # Ensure uniqueness
            if Spec.objects.filter(odin_id=new_odin_id).exists():
                new_odin_id = f"{spec.odin_id}_{timestamp}_2"

            new_spec = Spec.objects.create(
                odin_id=new_odin_id,
                title=f"{spec.title} (CLONE)",
                source=spec.source,
                content=spec.content,
                abandoned=False,  # Reset abandoned status
                board=spec.board,
                metadata=spec.metadata,
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

            for task in tasks:
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
                    depends_on=task.depends_on,
                    complexity=task.complexity,
                    metadata=task.metadata,
                )
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
    if board_id:
        qs = qs.filter(board_id=board_id)
    qs = _apply_date_range(qs, query_params, "created_at", "date_from", "date_to")
    return Response(TaskListSerializer(qs, many=True).data)


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
