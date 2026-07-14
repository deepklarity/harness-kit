from datetime import datetime
from pathlib import Path

from rest_framework import serializers
from django.utils import timezone

from .models import (
    Board, CommentAttachment, CommentType, Label, Notification, NotificationPreference,
    ReflectionReport, ScheduleKind, ScheduleStatus, Spec, SpecComment, Task,
    TaskComment, TaskHistory, TaskPriority, TaskSchedule, TaskScheduleRun,
    TaskStatus, User, UserSetting,
)
from .scheduling import ScheduleValidationError, parse_local_datetime

WEEKDAY_KEYS = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']

_FROZEN_STATUSES = {TaskStatus.DONE, TaskStatus.TESTING}


def _completed_at_from_history(obj, status_history):
    """Timestamp the task last transitioned into DONE/TESTING, or None."""
    if obj.status not in _FROZEN_STATUSES:
        return None
    for entry in reversed(status_history):
        if entry.new_value in _FROZEN_STATUSES:
            return entry.changed_at.isoformat()
    return None


def _time_in_statuses_from_history(obj, status_history):
    """Ms spent in each status, given ascending-ordered status history rows."""
    result = {}
    prev_status = None
    prev_time = obj.created_at
    for entry in status_history:
        if prev_status and prev_time:
            ms = (entry.changed_at - prev_time).total_seconds() * 1000
            result[prev_status] = result.get(prev_status, 0) + ms
        prev_status = entry.new_value
        prev_time = entry.changed_at
    # Account for time in current status — freeze for DONE/TESTING
    if prev_status and prev_time:
        ms = 0 if obj.status in _FROZEN_STATUSES else (timezone.now() - prev_time).total_seconds() * 1000
        result[prev_status] = result.get(prev_status, 0) + ms
    # Subtract question-pause time from EXECUTING
    if "EXECUTING" in result:
        metadata = obj.metadata or {}
        pause_ms = metadata.get("executing_paused_ms", 0)
        paused_at = metadata.get("question_paused_at")
        if paused_at:
            paused_start = datetime.fromisoformat(paused_at)
            if not paused_start.tzinfo:
                paused_start = paused_start.replace(tzinfo=timezone.utc)
            pause_ms += (timezone.now() - paused_start).total_seconds() * 1000
        result["EXECUTING"] = max(0, result["EXECUTING"] - pause_ms)
    return result


def _visible_scheduled_tasks(qs):
    # Mirrors views._exclude_hidden_scheduled_tasks: only future
    # occurrences hide; executed scheduled runs are visible history.
    return qs.exclude(
        schedule_id__isnull=False,
        schedule__status__in=[ScheduleStatus.ACTIVE, ScheduleStatus.PAUSED],
        status__in=[TaskStatus.BACKLOG, TaskStatus.TODO],
    )


class StrictUnknownFieldsMixin:
    """Reject any incoming field name that this serializer did not declare.

    DRF's default is to silently drop unknown keys — task #114 hit this when
    ``spec`` and ``assignee`` were silently dropped from POSTs to /tasks/,
    leaving the task with no spec/assignee and dispatch unable to route it.
    This mixin makes the API strict by default: unknown fields surface as
    a 400 response listing the rejected names so the client can fix the
    payload. Approved by F43/F44 dispatch guardrails.
    """

    def to_internal_value(self, data):
        if hasattr(data, "keys"):
            declared = set(self.fields.keys())
            incoming = set(data.keys())
            unknown = sorted(incoming - declared)
            if unknown:
                raise serializers.ValidationError({
                    "detail": f"Unknown field(s): {', '.join(unknown)}.",
                    "unknown_fields": unknown,
                })
        return super().to_internal_value(data)


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id", "name", "email", "color", "role", "available_models",
            "cost_tier", "capabilities", "cli_command", "default_model", "premium_model",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class LabelSerializer(serializers.ModelSerializer):
    board_id = serializers.PrimaryKeyRelatedField(
        queryset=Board.objects.all(), source="board", allow_null=True, required=False
    )

    class Meta:
        model = Label
        fields = ["id", "board_id", "name", "color", "created_at"]
        read_only_fields = ["id", "created_at"]


class TaskSerializer(serializers.ModelSerializer):
    assignee = UserSerializer(read_only=True)
    assignee_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(), source="assignee", write_only=True,
        required=False, allow_null=True,
    )
    labels = LabelSerializer(many=True, read_only=True)
    board_id = serializers.PrimaryKeyRelatedField(
        queryset=Board.objects.all(), source="board",
    )
    spec_id = serializers.IntegerField(required=False, allow_null=True)
    spec_odin_id = serializers.SerializerMethodField()
    estimated_cost_usd = serializers.SerializerMethodField()
    reflection_cost_usd = serializers.SerializerMethodField()
    usage = serializers.SerializerMethodField()
    time_in_statuses = serializers.SerializerMethodField()
    reference_images = serializers.SerializerMethodField()
    board_skip_reflection = serializers.SerializerMethodField()
    board_skip_proof = serializers.SerializerMethodField()
    board_escalation_enabled = serializers.SerializerMethodField()
    schedule_summary = serializers.SerializerMethodField()
    completed_at = serializers.SerializerMethodField()
    needs_human = serializers.SerializerMethodField()
    needs_human_reason = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            "id", "board_id", "title", "description", "dev_eta_seconds",
            "assignee_id", "assignee", "priority", "status", "created_by",
            "created_at", "last_updated_at", "labels", "kanban_position",
            "spec_id", "spec_odin_id", "depends_on",
            "complexity", "metadata", "model_name", "skip_reflection",
            "board_skip_reflection", "board_skip_proof", "board_escalation_enabled",
            "estimated_cost_usd", "reflection_cost_usd", "usage", "time_in_statuses",
            "reference_images",
            "schedule_summary", "completed_at",
            "needs_human", "needs_human_reason",
        ]
        read_only_fields = ["id", "created_at", "last_updated_at", "kanban_position"]

    def get_spec_odin_id(self, obj):
        return obj.spec.odin_id if obj.spec_id else None

    def get_board_skip_reflection(self, obj):
        return obj.board.skip_reflection if obj.board_id else False

    def get_board_skip_proof(self, obj):
        return obj.board.skip_proof if obj.board_id else False

    def get_board_escalation_enabled(self, obj):
        return obj.board.escalation_enabled if obj.board_id else False

    def get_usage(self, obj):
        from .execution_processing import compute_usage_from_trace
        usage = compute_usage_from_trace(obj)
        return usage if usage else None

    def get_estimated_cost_usd(self, obj):
        from .pricing import compute_task_estimated_cost
        return compute_task_estimated_cost(obj)

    def get_reflection_cost_usd(self, obj):
        """Sum cost of all completed reflections on this task."""
        from .pricing import estimate_task_cost
        total = 0.0
        has_any = False
        for r in obj.reflections.filter(status="COMPLETED"):
            usage = r.token_usage or {}
            cost = estimate_task_cost(
                r.reviewer_model,
                usage.get("input_tokens"),
                usage.get("output_tokens"),
            )
            if cost is not None:
                total += cost
                has_any = True
        return round(total, 6) if has_any else None

    def get_reference_images(self, obj):
        orphan_attachments = obj.attachments.filter(comment__isnull=True)
        return CommentAttachmentSerializer(
            orphan_attachments, many=True, context=self.context
        ).data

    def _status_history(self, obj):
        """Ordered (ascending) status-change history for this task.

        Queries obj.history directly. Subclasses that prefetch history
        (e.g. TaskKanbanCardSerializer) override this to read the prefetch
        cache instead, avoiding a per-task query.
        """
        return list(obj.history.filter(field_name="status").order_by("changed_at"))

    def get_completed_at(self, obj):
        """Return timestamp when task entered DONE/TESTING (for lifespan freeze).

        Returns None if the task is not currently in DONE or TESTING,
        allowing the frontend to use Date.now() for active tasks.
        When a task is dragged back from DONE/TESTING, this returns None
        so lifespan resumes growing.
        """
        return _completed_at_from_history(obj, self._status_history(obj))

    def get_time_in_statuses(self, obj):
        """Compute ms spent in each status from mutation history.

        Time freezes when the task is in DONE or TESTING — uses the
        timestamp of the last transition instead of now().
        Subtracts question-pause time from EXECUTING when an agent is
        waiting for a human answer.
        """
        return _time_in_statuses_from_history(obj, self._status_history(obj))

    def get_schedule_summary(self, obj):
        schedule = getattr(obj, "schedule", None)
        if not schedule:
            return None
        return {
            "id": schedule.id,
            "kind": schedule.kind,
            "status": schedule.status,
            "timezone": schedule.timezone,
            "next_run_at_utc": schedule.next_run_at_utc,
            "materialized_task_id": schedule.materialized_task_id,
            "current_run_id": obj.current_schedule_run_id,
        }

    def _needs_human_reason(self, obj):
        """Short reason a task needs a human, or "" when it doesn't.

        Priority order (first match wins) keeps the card indicator singular
        and unambiguous:

          1. merge_status == "needs_human" — merge conflict / stall escalated
          2. has_pending_question        — an unanswered question comment
          3. latest reflection ERROR      — reviewer failed, needs triage
          4. dispatch_blocked_reason set  — dispatch guardrail held the task

        The metadata signals are already on the row (no extra query). The
        reflection check walks the prefetched ``reflections`` (default
        ordering is ``-created_at``) so it only flags the *latest* verdict —
        a superseding PASS/NEEDS_WORK means the task was re-reviewed.
        """
        metadata = obj.metadata or {}
        if metadata.get("merge_status") == "needs_human":
            return "Merge needs human"
        if metadata.get("has_pending_question") is True:
            return "Question pending"
        for reflection in obj.reflections.all():
            if (reflection.verdict or "").upper() == "ERROR":
                return "Review errored"
            break
        if metadata.get("dispatch_blocked_reason"):
            return "Dispatch blocked"
        return ""

    def get_needs_human(self, obj):
        return bool(self._needs_human_reason(obj))

    def get_needs_human_reason(self, obj):
        return self._needs_human_reason(obj)

class CreateTaskSerializer(StrictUnknownFieldsMixin, serializers.Serializer):
    board_id = serializers.IntegerField()
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, default="")
    priority = serializers.ChoiceField(
        choices=TaskPriority.choices, required=False, default=TaskPriority.MEDIUM,
    )
    status = serializers.ChoiceField(
        choices=TaskStatus.choices, required=False, default=TaskStatus.TODO,
    )
    created_by = serializers.EmailField(required=False)
    created_by_user_id = serializers.IntegerField(required=False)
    assignee_id = serializers.IntegerField(required=False, allow_null=True)
    dev_eta_seconds = serializers.IntegerField(required=False, allow_null=True)
    label_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list,
    )
    spec_id = serializers.IntegerField(required=False)
    depends_on = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    complexity = serializers.CharField(max_length=20, required=False)
    metadata = serializers.JSONField(required=False, default=dict)
    model_name = serializers.CharField(max_length=255, required=False, allow_null=True, allow_blank=True)
    skip_reflection = serializers.BooleanField(required=False, default=False)

    def validate(self, data):
        if not data.get("created_by") and not data.get("created_by_user_id"):
            raise serializers.ValidationError(
                "Either created_by (email) or created_by_user_id is required."
            )
        return data


class ReworkTaskSerializer(StrictUnknownFieldsMixin, serializers.Serializer):
    instruction = serializers.CharField()
    created_by = serializers.EmailField(required=False)
    created_by_user_id = serializers.IntegerField(required=False)

    def validate(self, data):
        if not data.get("created_by") and not data.get("created_by_user_id"):
            raise serializers.ValidationError(
                "Either created_by (email) or created_by_user_id is required."
            )
        return data


class UpdateTaskSerializer(StrictUnknownFieldsMixin, serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False)
    dev_eta_seconds = serializers.IntegerField(required=False, allow_null=True)
    priority = serializers.ChoiceField(choices=TaskPriority.choices, required=False)
    status = serializers.ChoiceField(choices=TaskStatus.choices, required=False)
    assignee_id = serializers.IntegerField(required=False, allow_null=True)
    spec_id = serializers.IntegerField(required=False, allow_null=True)
    label_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, allow_empty=True
    )
    updated_by = serializers.EmailField()
    depends_on = serializers.ListField(child=serializers.CharField(), required=False)
    complexity = serializers.CharField(max_length=20, required=False, allow_null=True)
    metadata = serializers.JSONField(required=False)
    model_name = serializers.CharField(max_length=255, required=False, allow_null=True, allow_blank=True)
    kanban_target_index = serializers.IntegerField(required=False, min_value=0)
    kanban_target_status = serializers.ChoiceField(choices=TaskStatus.choices, required=False)
    skip_reflection = serializers.BooleanField(required=False)


class AssignTaskSerializer(serializers.Serializer):
    assignee_id = serializers.IntegerField()
    updated_by = serializers.EmailField()


class UnassignTaskSerializer(serializers.Serializer):
    updated_by = serializers.EmailField()


class AddLabelsSerializer(serializers.Serializer):
    label_ids = serializers.ListField(child=serializers.IntegerField())
    updated_by = serializers.EmailField()


class SetClaudeTokenSerializer(serializers.Serializer):
    """Input validator for POST /boards/<id>/claude-token/ (write-only)."""
    token = serializers.CharField(required=True, allow_blank=False, trim_whitespace=True)


class BoardSerializer(serializers.ModelSerializer):
    member_ids = serializers.SerializerMethodField()
    agents = serializers.SerializerMethodField()
    claude_token_configured = serializers.SerializerMethodField()

    class Meta:
        model = Board
        fields = ["id", "name", "description", "is_trial", "working_dir", "timezone", "odin_initialized", "skip_reflection", "skip_proof", "auto_start_planned_tasks", "reflection_model", "reflection_review_strategy", "model_escalation_priority", "reviewer_order", "escalation_enabled", "failure_max_retries", "routing_policy", "allow_project_root_execution", "created_at", "updated_at", "member_ids", "agents", "claude_token_configured"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_member_ids(self, obj):
        return list(obj.memberships.values_list("user_id", flat=True))

    def get_claude_token_configured(self, obj):
        """Whether a .claude-token file exists in the board's working_dir.
        Never exposes the token value — only its presence."""
        if not obj.working_dir:
            return False
        try:
            return (Path(obj.working_dir) / ".claude-token").is_file()
        except OSError:
            return False

    def get_agents(self, obj):
        memberships = obj.memberships.filter(user__role="AGENT").select_related("user")
        all_agents = User.objects.filter(role="AGENT")
        member_user_ids = {m.user_id for m in memberships}

        agents = []
        for agent_user in all_agents:
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
        return agents


class CreateBoardSerializer(StrictUnknownFieldsMixin, serializers.ModelSerializer):
    auto_init = serializers.BooleanField(default=True, required=False)
    disabled_agents = serializers.ListField(
        child=serializers.CharField(), required=False, default=list,
    )
    directory_mode = serializers.ChoiceField(
        choices=["existing", "create"], required=False, default="existing",
    )
    parent_directory = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    directory_name = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    class Meta:
        model = Board
        fields = [
            "name", "description", "is_trial", "working_dir", "timezone", "auto_init", "disabled_agents",
            "directory_mode", "parent_directory", "directory_name",
        ]
        extra_kwargs = {
            "description": {"required": False, "default": ""},
            "is_trial": {"required": False, "default": False},
            "working_dir": {"required": False, "allow_null": True, "allow_blank": True},
            "timezone": {"required": False, "default": "UTC"},
        }

    def validate_working_dir(self, value):
        if value in (None, ""):
            return None
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        # `directory_mode` has a default of "existing", so attrs.get() always
        # returns a value. Check the user's initial payload instead — if they
        # didn't send any directory fields at all, the board is allowed to
        # have a null working_dir (the model is nullable).
        initial = self.initial_data if hasattr(self, "initial_data") else {}
        user_supplied_any_directory_field = any(
            key in initial for key in ("directory_mode", "working_dir",
                                       "parent_directory", "directory_name")
        )
        if not user_supplied_any_directory_field:
            return attrs

        mode = attrs.get("directory_mode")
        working_dir = attrs.get("working_dir")
        parent_directory = attrs.get("parent_directory")
        directory_name = attrs.get("directory_name")

        # Implicit mode from the fields the caller actually supplied.
        if not mode:
            mode = "existing" if working_dir else "create"

        if mode == "existing":
            if not working_dir:
                raise serializers.ValidationError({"working_dir": "This field is required when using an existing directory."})
            attrs["parent_directory"] = None
            attrs["directory_name"] = None
            return attrs

        if mode == "create":
            if not parent_directory:
                raise serializers.ValidationError({"parent_directory": "This field is required when creating a new directory."})
            if not directory_name:
                raise serializers.ValidationError({"directory_name": "This field is required when creating a new directory."})
            attrs["working_dir"] = None
            return attrs

        raise serializers.ValidationError({"directory_mode": "Invalid directory mode."})


class BoardListSerializer(BoardSerializer):
    member_count = serializers.IntegerField(read_only=True)
    task_count = serializers.IntegerField(read_only=True)

    class Meta(BoardSerializer.Meta):
        fields = BoardSerializer.Meta.fields + ["member_count", "task_count"]


class BoardDetailSerializer(BoardSerializer):
    tasks = serializers.SerializerMethodField()

    class Meta(BoardSerializer.Meta):
        fields = BoardSerializer.Meta.fields + ["tasks"]

    def get_tasks(self, obj):
        qs = _visible_scheduled_tasks(obj.tasks.all().select_related("assignee").prefetch_related("labels"))
        return TaskSerializer(qs, many=True, context=self.context).data


class SpecCommentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SpecComment
        fields = [
            "id", "spec_id", "author_email", "author_label",
            "content", "attachments", "comment_type", "created_at",
        ]
        read_only_fields = ["id", "spec_id", "created_at"]


class SpecSerializer(serializers.ModelSerializer):
    tasks = serializers.SerializerMethodField()
    comments = SpecCommentSerializer(many=True, read_only=True)
    board_id = serializers.PrimaryKeyRelatedField(
        queryset=Board.objects.all(), source="board",
    )
    cost_summary = serializers.SerializerMethodField()
    merge_summary = serializers.SerializerMethodField()

    class Meta:
        model = Spec
        fields = [
            "id", "odin_id", "title", "source", "content", "abandoned",
            "board_id", "metadata", "created_at", "tasks",
            "comments", "cost_summary", "merge_summary", "status", "planner_config",
        ]
        read_only_fields = ["id", "created_at"]

    def get_tasks(self, obj):
        qs = _visible_scheduled_tasks(obj.tasks.all().select_related("assignee").prefetch_related("labels"))
        return TaskSerializer(qs, many=True, context=self.context).data

    def get_cost_summary(self, obj):
        from .pricing import compute_spec_cost_summary
        return compute_spec_cost_summary(obj.tasks.all())

    def get_merge_summary(self, obj):
        from .pricing import compute_spec_merge_summary
        return compute_spec_merge_summary(obj.tasks.all())


class SpecListSerializer(serializers.ModelSerializer):
    board_id = serializers.PrimaryKeyRelatedField(
        queryset=Board.objects.all(), source="board",
    )
    task_count = serializers.IntegerField(read_only=True)
    cost_summary = serializers.SerializerMethodField()
    merge_summary = serializers.SerializerMethodField()

    class Meta:
        model = Spec
        fields = [
            "id", "odin_id", "title", "source", "content", "abandoned",
            "board_id", "metadata", "created_at", "task_count", "cost_summary",
            "merge_summary", "status", "planner_config",
        ]
        read_only_fields = ["id", "created_at"]

    def get_cost_summary(self, obj):
        from .pricing import compute_spec_cost_summary
        return compute_spec_cost_summary(obj.tasks.all())

    def get_merge_summary(self, obj):
        from .pricing import compute_spec_merge_summary
        return compute_spec_merge_summary(obj.tasks.all())


class CreateSpecSerializer(serializers.ModelSerializer):
    board_id = serializers.PrimaryKeyRelatedField(
        queryset=Board.objects.all(), source="board",
    )

    class Meta:
        model = Spec
        fields = ["odin_id", "title", "source", "content", "board_id", "metadata", "status"]
        extra_kwargs = {
            "source": {"default": "inline"},
            "content": {"default": ""},
            "metadata": {"default": dict},
            "status": {"required": False},
        }



class CreatePlanningSpecSerializer(serializers.ModelSerializer):
    """Serializer for UI-initiated planning spec creation."""
    board_id = serializers.PrimaryKeyRelatedField(queryset=Board.objects.all(), source="board")

    class Meta:
        model = Spec
        fields = ["title", "content", "board_id", "planner_config"]
        extra_kwargs = {
            "content": {"default": ""},
            "planner_config": {"default": dict},
        }




class PlanningResultSerializer(serializers.Serializer):
    """Validates the planning trace payload from odin plan."""
    raw_output = serializers.CharField(allow_blank=True)
    duration_ms = serializers.FloatField()
    agent = serializers.CharField()
    model = serializers.CharField(allow_blank=True, default="")
    effective_input = serializers.CharField(allow_blank=True, required=False, default="")
    success = serializers.BooleanField()


class BoardMemberIdsSerializer(serializers.Serializer):
    user_ids = serializers.ListField(child=serializers.IntegerField())


class CommentAttachmentSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = CommentAttachment
        fields = [
            "id", "url", "original_filename", "content_type",
            "file_size", "uploaded_by", "created_at",
        ]
        read_only_fields = fields

    def get_url(self, obj):
        request = self.context.get("request")
        if request and obj.file:
            return request.build_absolute_uri(obj.file.url)
        return obj.file.url if obj.file else None


class TaskCommentSerializer(serializers.ModelSerializer):
    file_attachments = CommentAttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = TaskComment
        fields = [
            "id", "task_id", "author_email", "author_label",
            "content", "attachments", "comment_type", "created_at",
            "file_attachments",
        ]
        read_only_fields = ["id", "task_id", "created_at"]


class CreateTaskCommentSerializer(serializers.Serializer):
    author_email = serializers.EmailField()
    author_label = serializers.CharField(max_length=255, required=False, default="", allow_blank=True)
    content = serializers.CharField()
    attachments = serializers.ListField(child=serializers.JSONField(), required=False, default=list)
    comment_type = serializers.ChoiceField(
        choices=CommentType.choices, required=False, default=CommentType.STATUS_UPDATE,
    )
    attachment_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list,
    )


class TaskHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskHistory
        fields = [
            "id", "task_id", "field_name", "old_value", "new_value",
            "changed_at", "changed_by",
        ]


class TaskDashboardSerializer(TaskSerializer):
    """Dashboard list: includes history for timeline/activity, but NOT comments.
    Includes comment_count so the frontend can show unseen-comment badges
    without fetching full comment payloads."""
    history = TaskHistorySerializer(many=True, read_only=True)
    comment_count = serializers.IntegerField(read_only=True)

    class Meta(TaskSerializer.Meta):
        fields = TaskSerializer.Meta.fields + ["history", "comment_count"]


class TaskListSerializer(TaskSerializer):
    comment_count = serializers.IntegerField(read_only=True)

    class Meta(TaskSerializer.Meta):
        fields = TaskSerializer.Meta.fields + ["comment_count"]


# Fields TaskCard never renders (see taskit-frontend TaskCard.tsx) — each one
# costs an extra per-task query (comment scan for trace parsing, reflection
# cost sum, board FK, attachments) when computed via TaskSerializer's
# SerializerMethodFields. The task detail modal fetches these itself, so the
# Kanban board — which lists a full page of tasks per lane — excludes them.
_KANBAN_CARD_EXCLUDED_FIELDS = {
    "estimated_cost_usd", "reflection_cost_usd", "usage", "reference_images",
    "board_skip_reflection", "board_skip_proof", "board_escalation_enabled",
    "schedule_summary", "spec_odin_id",
}


class TaskKanbanCardSerializer(TaskListSerializer):
    """Lightweight task serializer for Kanban lane cards.

    time_in_statuses/completed_at still read obj.history, but rely on it
    being prefetched (see the `kanban` view's queryset) so no per-task
    query is issued for either field.
    """

    class Meta(TaskListSerializer.Meta):
        fields = [f for f in TaskListSerializer.Meta.fields if f not in _KANBAN_CARD_EXCLUDED_FIELDS]

    def _status_history(self, obj):
        # obj.history.all() hits the prefetch cache (no query) as long as
        # the queryset prefetched `history`; falls back to a live query
        # (via the base class) if this serializer is ever used unprefetched.
        if not hasattr(obj, "_prefetched_objects_cache") or "history" not in obj._prefetched_objects_cache:
            return super()._status_history(obj)
        return sorted(
            (h for h in obj.history.all() if h.field_name == "status"),
            key=lambda h: h.changed_at,
        )


class TaskSearchResultSerializer(serializers.Serializer):
    task_id = serializers.IntegerField()
    title = serializers.CharField()
    status = serializers.CharField()
    board_id = serializers.IntegerField()
    board_name = serializers.CharField()
    spec_id = serializers.IntegerField(allow_null=True)
    spec_title = serializers.CharField(allow_null=True)


class MemberListSerializer(UserSerializer):
    task_count = serializers.IntegerField(read_only=True)

    class Meta(UserSerializer.Meta):
        fields = UserSerializer.Meta.fields + ["task_count"]


class UserSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserSetting
        fields = ["preferred_ide_id", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]


class TaskWithHistorySerializer(TaskSerializer):
    history = TaskHistorySerializer(many=True, read_only=True)
    comments = TaskCommentSerializer(many=True, read_only=True)

    class Meta(TaskSerializer.Meta):
        fields = TaskSerializer.Meta.fields + ["history", "comments"]


class TaskDetailSerializer(TaskWithHistorySerializer):
    """Single task detail: history + comments + spec_title + twins + quote."""
    spec_title = serializers.SerializerMethodField()
    twins = serializers.SerializerMethodField()
    estimate = serializers.SerializerMethodField()
    actual = serializers.SerializerMethodField()

    class Meta(TaskWithHistorySerializer.Meta):
        fields = TaskWithHistorySerializer.Meta.fields + [
            "spec_title", "twins", "estimate", "actual",
        ]

    def get_spec_title(self, obj):
        return obj.spec.title if obj.spec_id else None

    def get_twins(self, obj):
        from .similarity import find_twins
        return find_twins(obj)

    def get_estimate(self, obj):
        # The dispatch-time quote, stamped on metadata by
        # estimation.stamp_estimate. Exposed as structured data so the
        # UI never has to parse the dispatch comment markdown.
        metadata = obj.metadata or {}
        return metadata.get("estimate")

    def get_actual(self, obj):
        # The completion-time actual cost, stamped by
        # estimation.stamp_actual. Paired with `estimate` for the
        # quote-vs-actual trail.
        metadata = obj.metadata or {}
        return metadata.get("actual")


class SpecDiagnosticSerializer(serializers.ModelSerializer):
    """Full spec with nested task details (history + comments) for diagnostics."""
    tasks = TaskDetailSerializer(many=True, read_only=True)
    comments = SpecCommentSerializer(many=True, read_only=True)
    board_name = serializers.CharField(source="board.name", read_only=True)
    cost_summary = serializers.SerializerMethodField()
    merge_summary = serializers.SerializerMethodField()

    class Meta:
        model = Spec
        fields = [
            "id", "odin_id", "title", "source", "content", "abandoned",
            "board_id", "board_name", "metadata", "created_at", "tasks",
            "comments", "cost_summary", "merge_summary",
        ]
        read_only_fields = ["id", "created_at"]

    def get_cost_summary(self, obj):
        from .pricing import compute_spec_cost_summary
        return compute_spec_cost_summary(obj.tasks.all())

    def get_merge_summary(self, obj):
        from .pricing import compute_spec_merge_summary
        return compute_spec_merge_summary(obj.tasks.all())


# Derived from agent_models.json (via get_active_agents) rather than
# hardcoded, so retiring/curating a provider there automatically removes it
# from valid reflection reviewers here too — no second list to go stale.
def _reflection_allowed_agents():
    from .pricing import get_active_agents
    return get_active_agents()


REFLECTION_ALLOWED_AGENTS = _reflection_allowed_agents()


class ReflectionRequestSerializer(serializers.Serializer):
    reviewer_agent = serializers.CharField(default="claude")
    reviewer_model = serializers.CharField(default="claude-opus-4-8")
    custom_prompt = serializers.CharField(required=False, allow_blank=True, default="")
    context_selections = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=["description", "comments", "execution_result", "dependencies", "metadata"],
    )
    requested_by = serializers.EmailField(required=False, default="")

    def validate_reviewer_agent(self, value):
        if (value or "").strip().lower() not in REFLECTION_ALLOWED_AGENTS:
            allowed = ", ".join(sorted(REFLECTION_ALLOWED_AGENTS))
            raise serializers.ValidationError(
                f"Agent '{value}' is not supported as a reflection reviewer. Allowed: {allowed}."
            )
        return value


class UserIdeSettingUpdateSerializer(serializers.Serializer):
    preferred_ide_id = serializers.CharField(max_length=50, allow_null=True, allow_blank=True, required=False)


class OpenProjectSerializer(serializers.Serializer):
    ide_id = serializers.CharField(max_length=50, required=False, allow_blank=True, allow_null=True)


class ReflectionReportSerializer(serializers.ModelSerializer):
    task_title = serializers.CharField(source="task.title", read_only=True)
    estimated_cost_usd = serializers.SerializerMethodField()

    class Meta:
        model = ReflectionReport
        fields = "__all__"

    def get_estimated_cost_usd(self, obj):
        """Estimate reviewer cost from token_usage and reviewer_model."""
        from .pricing import estimate_task_cost
        usage = obj.token_usage or {}
        if not usage:
            return None
        return estimate_task_cost(
            obj.reviewer_model,
            usage.get("input_tokens"),
            usage.get("output_tokens"),
        )


class ReflectionReportUpdateSerializer(serializers.Serializer):
    """Used by Odin to submit reflection results."""
    status = serializers.ChoiceField(choices=["RUNNING", "COMPLETED", "FAILED"])
    quality_assessment = serializers.CharField(required=False, allow_blank=True, default="")
    slop_detection = serializers.CharField(required=False, allow_blank=True, default="")
    improvements = serializers.CharField(required=False, allow_blank=True, default="")
    agent_optimization = serializers.CharField(required=False, allow_blank=True, default="")
    quota_failure = serializers.CharField(required=False, allow_blank=True, default="")
    verdict = serializers.CharField(required=False, allow_blank=True, default="")
    verdict_summary = serializers.CharField(required=False, allow_blank=True, default="")
    raw_output = serializers.CharField(required=False, allow_blank=True, default="")
    execution_trace = serializers.CharField(required=False, allow_blank=True, default="")
    duration_ms = serializers.IntegerField(required=False, allow_null=True, default=None)
    token_usage = serializers.JSONField(required=False, default=dict)
    error_message = serializers.CharField(required=False, allow_blank=True, default="")
    assembled_prompt = serializers.CharField(required=False, allow_blank=True, default="")


class ExecutionResultPayloadSerializer(serializers.Serializer):
    """Inner payload describing the raw execution result from an agent."""
    success = serializers.BooleanField()
    raw_output = serializers.CharField(allow_blank=True, default="")
    error = serializers.CharField(allow_null=True, required=False, default=None)
    duration_ms = serializers.FloatField(allow_null=True, required=False, default=None)
    agent = serializers.CharField(allow_blank=True, required=False, default="")
    metadata = serializers.JSONField(required=False, default=dict)
    failure_type = serializers.CharField(allow_blank=True, required=False, default="")
    failure_reason = serializers.CharField(allow_blank=True, required=False, default="")
    failure_origin = serializers.CharField(allow_blank=True, required=False, default="")
    failure_phase = serializers.CharField(allow_blank=True, required=False, default="")


class ExecutionResultSerializer(serializers.Serializer):
    """Top-level serializer for POST /tasks/:id/execution_result/."""
    execution_result = ExecutionResultPayloadSerializer()
    status = serializers.ChoiceField(choices=TaskStatus.choices)
    updated_by = serializers.EmailField()


class StopExecutionSerializer(serializers.Serializer):
    """Payload for POST /tasks/:id/stop_execution/."""
    updated_by = serializers.EmailField()
    target_status = serializers.ChoiceField(choices=TaskStatus.choices)
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class RuntimeStopSerializer(serializers.Serializer):
    """Payload for POST /api/runtime/stop/."""
    task_id = serializers.IntegerField()
    updated_by = serializers.EmailField()
    target_status = serializers.ChoiceField(choices=TaskStatus.choices, required=False, default=TaskStatus.TODO)
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    force = serializers.BooleanField(required=False, default=False)


class ScheduleTemplateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, default="")
    priority = serializers.ChoiceField(
        choices=TaskPriority.choices, required=False, default=TaskPriority.MEDIUM,
    )
    assignee_id = serializers.IntegerField(required=False, allow_null=True)
    model_name = serializers.CharField(max_length=255, required=False, allow_null=True, allow_blank=True)
    label_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    depends_on = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    dev_eta_seconds = serializers.IntegerField(required=False, allow_null=True)
    spec_id = serializers.IntegerField(required=False, allow_null=True)
    metadata = serializers.JSONField(required=False, default=dict)


class ScheduleRuleSerializer(serializers.Serializer):
    freq = serializers.ChoiceField(choices=["DAILY", "WEEKLY", "MONTHLY"])
    interval = serializers.IntegerField(min_value=1, required=False, default=1)
    by_weekday = serializers.ListField(
        child=serializers.ChoiceField(choices=["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]),
        required=False, default=list,
    )
    by_monthday = serializers.ListField(
        child=serializers.IntegerField(min_value=1, max_value=31),
        required=False, default=list,
    )
    end_mode = serializers.ChoiceField(choices=["NEVER", "ON_DATE", "AFTER_COUNT"], required=False, default="NEVER")
    until_local = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    occurrence_count = serializers.IntegerField(required=False, allow_null=True, min_value=1)


class CreateScheduleSerializer(serializers.Serializer):
    board_id = serializers.IntegerField()
    kind = serializers.ChoiceField(choices=ScheduleKind.choices)
    timezone = serializers.CharField(max_length=64)
    starts_at_local = serializers.CharField()
    template = ScheduleTemplateSerializer()
    recurrence_rule = ScheduleRuleSerializer(required=False)
    created_by = serializers.EmailField(required=False)
    created_by_user_id = serializers.IntegerField(required=False)

    def validate(self, data):
        if not data.get("created_by") and not data.get("created_by_user_id"):
            raise serializers.ValidationError(
                "Either created_by (email) or created_by_user_id is required."
            )
        try:
            starts_at_local = parse_local_datetime(data["starts_at_local"], data["timezone"])
        except ScheduleValidationError as exc:
            raise serializers.ValidationError({exc.field: exc.message}) from exc
        if starts_at_local <= timezone.now().astimezone(starts_at_local.tzinfo):
            raise serializers.ValidationError({
                "starts_at_local": "Scheduled start time must be in the future.",
            })
        if data["kind"] == ScheduleKind.RECURRING and not data.get("recurrence_rule"):
            raise serializers.ValidationError({"recurrence_rule": "Required for recurring schedules."})
        if data["kind"] == ScheduleKind.RECURRING:
            recurrence = data.get("recurrence_rule") or {}
            if recurrence.get("freq") == "WEEKLY":
                weekdays = recurrence.get("by_weekday") or []
                expected = WEEKDAY_KEYS[starts_at_local.weekday()]
                if weekdays and expected not in weekdays:
                    raise serializers.ValidationError({
                        "recurrence_rule": f"Weekly recurrence must include the first scheduled weekday: {expected}.",
                    })
            if recurrence.get("freq") == "MONTHLY":
                monthdays = recurrence.get("by_monthday") or []
                if monthdays and starts_at_local.day not in monthdays:
                    raise serializers.ValidationError({
                        "recurrence_rule": f"Monthly recurrence must include the first scheduled day: {starts_at_local.day}.",
                    })
        return data


class UpdateScheduleSerializer(serializers.Serializer):
    timezone = serializers.CharField(max_length=64, required=False)
    starts_at_local = serializers.CharField(required=False)
    template = ScheduleTemplateSerializer(required=False)
    recurrence_rule = ScheduleRuleSerializer(required=False)

    def validate(self, data):
        tz_name = data.get("timezone")
        starts_at_local = data.get("starts_at_local")
        if tz_name:
            try:
                if starts_at_local:
                    parsed = parse_local_datetime(starts_at_local, tz_name)
                    if parsed <= timezone.now().astimezone(parsed.tzinfo):
                        raise serializers.ValidationError({
                            "starts_at_local": "Scheduled start time must be in the future.",
                        })
                else:
                    parse_local_datetime(timezone.now(), tz_name)
            except ScheduleValidationError as exc:
                raise serializers.ValidationError({exc.field: exc.message}) from exc
        elif starts_at_local:
            try:
                datetime.fromisoformat(str(starts_at_local).strip())
            except ValueError as exc:
                raise serializers.ValidationError({
                    "starts_at_local": "Invalid datetime format. Use ISO 8601.",
                }) from exc
        return data


class TaskScheduleRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskScheduleRun
        fields = [
            "id", "run_number", "task_id", "scheduled_for_utc", "released_at_utc",
            "finished_at_utc", "status", "terminal_task_status", "release_reason",
            "result_summary", "template_snapshot", "created_at",
        ]


class TaskScheduleSerializer(serializers.ModelSerializer):
    template = serializers.SerializerMethodField()
    runs = TaskScheduleRunSerializer(many=True, read_only=True)

    class Meta:
        model = TaskSchedule
        fields = [
            "id", "board_id", "kind", "status", "timezone", "starts_at_local",
            "starts_at_utc", "next_run_at_utc", "recurrence_rule", "materialized_task_id",
            "last_released_run_id", "paused_at", "canceled_at", "completed_at",
            "created_by", "created_at", "updated_at", "template", "runs",
        ]

    def get_template(self, obj):
        return {
            "title": obj.template_title,
            "description": obj.template_description,
            "priority": obj.template_priority,
            "assignee_id": obj.template_assignee_id,
            "model_name": obj.template_model_name,
            "label_ids": obj.template_label_ids or [],
            "depends_on": obj.template_depends_on or [],
            "dev_eta_seconds": obj.template_dev_eta_seconds,
            "spec_id": obj.template_spec_id,
            "metadata": obj.template_metadata or {},
        }


class ScheduleStatusMutationSerializer(serializers.Serializer):
    updated_by = serializers.EmailField(required=False, default="")


class RoutingModelSerializer(serializers.Serializer):
    """A single model within a routing-config agent entry."""
    name = serializers.CharField()
    enabled = serializers.BooleanField()
    is_default = serializers.BooleanField()
    description = serializers.CharField()


class RoutingAgentSerializer(serializers.Serializer):
    """Agent entry in the routing-config response."""
    name = serializers.CharField()
    cost_tier = serializers.CharField()
    capabilities = serializers.ListField(child=serializers.CharField())
    default_model = serializers.CharField(allow_null=True)
    premium_model = serializers.CharField(allow_null=True)
    models = RoutingModelSerializer(many=True)


class ModelToggleSerializer(serializers.Serializer):
    """Payload for toggling a model's enabled state."""
    enabled = serializers.BooleanField()


class NotificationSerializer(serializers.ModelSerializer):
    task_title = serializers.CharField(source="task.title", read_only=True, default=None)
    spec_title = serializers.CharField(source="spec.title", read_only=True, default=None)
    board_name = serializers.CharField(source="board.name", read_only=True, default=None)

    class Meta:
        model = Notification
        fields = [
            "id", "recipient", "notification_type", "title", "body",
            "task", "task_title", "spec", "spec_title",
            "board", "board_name", "actor_email",
            "is_read", "created_at",
        ]
        read_only_fields = ["id", "recipient", "created_at"]


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = [
            "desktop_enabled", "sound_enabled", "disabled_types",
            "quiet_hours_start", "quiet_hours_end", "quiet_hours_timezone",
        ]
