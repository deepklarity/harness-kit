from rest_framework import serializers

from .models import Board, CommentAttachment, CommentType, Label, ReflectionReport, Spec, SpecComment, Task, TaskComment, TaskHistory, TaskPriority, TaskStatus, User


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
    class Meta:
        model = Label
        fields = ["id", "name", "color", "created_at"]
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
    estimated_cost_usd = serializers.SerializerMethodField()
    reflection_cost_usd = serializers.SerializerMethodField()
    usage = serializers.SerializerMethodField()
    time_in_statuses = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            "id", "board_id", "title", "description", "dev_eta_seconds",
            "assignee_id", "assignee", "priority", "status", "created_by",
            "created_at", "last_updated_at", "labels", "kanban_position",
            "spec_id", "depends_on",
            "complexity", "metadata", "model_name",
            "estimated_cost_usd", "reflection_cost_usd", "usage", "time_in_statuses",
        ]
        read_only_fields = ["id", "created_at", "last_updated_at", "kanban_position"]

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

    def get_time_in_statuses(self, obj):
        """Compute ms spent in each status from mutation history."""
        from django.utils import timezone
        history = obj.history.filter(field_name="status").order_by("changed_at")
        result = {}
        prev_status = None
        prev_time = obj.created_at
        for entry in history:
            if prev_status and prev_time:
                ms = (entry.changed_at - prev_time).total_seconds() * 1000
                result[prev_status] = result.get(prev_status, 0) + ms
            prev_status = entry.new_value
            prev_time = entry.changed_at
        # Account for time in current status
        if prev_status and prev_time:
            ms = (timezone.now() - prev_time).total_seconds() * 1000
            result[prev_status] = result.get(prev_status, 0) + ms
        return result


class CreateTaskSerializer(serializers.Serializer):
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

    def validate(self, data):
        if not data.get("created_by") and not data.get("created_by_user_id"):
            raise serializers.ValidationError(
                "Either created_by (email) or created_by_user_id is required."
            )
        return data


class UpdateTaskSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False)
    dev_eta_seconds = serializers.IntegerField(required=False, allow_null=True)
    priority = serializers.ChoiceField(choices=TaskPriority.choices, required=False)
    status = serializers.ChoiceField(choices=TaskStatus.choices, required=False)
    assignee_id = serializers.IntegerField(required=False, allow_null=True)
    label_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False,
    )
    updated_by = serializers.EmailField()
    depends_on = serializers.ListField(child=serializers.CharField(), required=False)
    complexity = serializers.CharField(max_length=20, required=False, allow_null=True)
    metadata = serializers.JSONField(required=False)
    model_name = serializers.CharField(max_length=255, required=False, allow_null=True, allow_blank=True)
    kanban_target_index = serializers.IntegerField(required=False, min_value=0)
    kanban_target_status = serializers.ChoiceField(choices=TaskStatus.choices, required=False)


class AssignTaskSerializer(serializers.Serializer):
    assignee_id = serializers.IntegerField()
    updated_by = serializers.EmailField()


class UnassignTaskSerializer(serializers.Serializer):
    updated_by = serializers.EmailField()


class AddLabelsSerializer(serializers.Serializer):
    label_ids = serializers.ListField(child=serializers.IntegerField())
    updated_by = serializers.EmailField()


class BoardSerializer(serializers.ModelSerializer):
    member_ids = serializers.SerializerMethodField()

    class Meta:
        model = Board
        fields = ["id", "name", "description", "is_trial", "working_dir", "odin_initialized", "created_at", "updated_at", "member_ids"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_member_ids(self, obj):
        return list(obj.memberships.values_list("user_id", flat=True))


class CreateBoardSerializer(serializers.ModelSerializer):
    auto_init = serializers.BooleanField(default=True, required=False)
    disabled_agents = serializers.ListField(
        child=serializers.CharField(), required=False, default=list,
    )

    class Meta:
        model = Board
        fields = ["name", "description", "is_trial", "working_dir", "auto_init", "disabled_agents"]
        extra_kwargs = {
            "description": {"required": False, "default": ""},
            "is_trial": {"required": False, "default": False},
            "working_dir": {"required": False, "allow_null": True, "allow_blank": True},
        }

    def validate_working_dir(self, value):
        if value in (None, ""):
            return None
        return value


class BoardListSerializer(BoardSerializer):
    member_count = serializers.IntegerField(read_only=True)

    class Meta(BoardSerializer.Meta):
        fields = BoardSerializer.Meta.fields + ["member_count"]


class BoardDetailSerializer(BoardSerializer):
    tasks = TaskSerializer(many=True, read_only=True)

    class Meta(BoardSerializer.Meta):
        fields = BoardSerializer.Meta.fields + ["tasks"]


class SpecCommentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SpecComment
        fields = [
            "id", "spec_id", "author_email", "author_label",
            "content", "attachments", "comment_type", "created_at",
        ]
        read_only_fields = ["id", "spec_id", "created_at"]


class SpecSerializer(serializers.ModelSerializer):
    tasks = TaskSerializer(many=True, read_only=True)
    comments = SpecCommentSerializer(many=True, read_only=True)
    board_id = serializers.PrimaryKeyRelatedField(
        queryset=Board.objects.all(), source="board",
    )
    cost_summary = serializers.SerializerMethodField()

    class Meta:
        model = Spec
        fields = [
            "id", "odin_id", "title", "source", "content", "abandoned",
            "board_id", "metadata", "created_at", "tasks",
            "comments", "cost_summary",
        ]
        read_only_fields = ["id", "created_at"]

    def get_cost_summary(self, obj):
        from .pricing import compute_spec_cost_summary
        return compute_spec_cost_summary(obj.tasks.all())


class SpecListSerializer(serializers.ModelSerializer):
    board_id = serializers.PrimaryKeyRelatedField(
        queryset=Board.objects.all(), source="board",
    )
    task_count = serializers.IntegerField(read_only=True)
    cost_summary = serializers.SerializerMethodField()

    class Meta:
        model = Spec
        fields = [
            "id", "odin_id", "title", "source", "content", "abandoned",
            "board_id", "metadata", "created_at", "task_count", "cost_summary",
        ]
        read_only_fields = ["id", "created_at"]

    def get_cost_summary(self, obj):
        from .pricing import compute_spec_cost_summary
        return compute_spec_cost_summary(obj.tasks.all())


class CreateSpecSerializer(serializers.ModelSerializer):
    board_id = serializers.PrimaryKeyRelatedField(
        queryset=Board.objects.all(), source="board",
    )

    class Meta:
        model = Spec
        fields = ["odin_id", "title", "source", "content", "board_id", "metadata"]
        extra_kwargs = {
            "source": {"default": "inline"},
            "content": {"default": ""},
            "metadata": {"default": dict},
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


class MemberListSerializer(UserSerializer):
    task_count = serializers.IntegerField(read_only=True)

    class Meta(UserSerializer.Meta):
        fields = UserSerializer.Meta.fields + ["task_count"]


class TaskWithHistorySerializer(TaskSerializer):
    history = TaskHistorySerializer(many=True, read_only=True)
    comments = TaskCommentSerializer(many=True, read_only=True)

    class Meta(TaskSerializer.Meta):
        fields = TaskSerializer.Meta.fields + ["history", "comments"]


class TaskDetailSerializer(TaskWithHistorySerializer):
    """Single task detail: history + comments + spec_title + estimated cost."""
    spec_title = serializers.SerializerMethodField()

    class Meta(TaskWithHistorySerializer.Meta):
        fields = TaskWithHistorySerializer.Meta.fields + ["spec_title"]

    def get_spec_title(self, obj):
        return obj.spec.title if obj.spec_id else None


class SpecDiagnosticSerializer(serializers.ModelSerializer):
    """Full spec with nested task details (history + comments) for diagnostics."""
    tasks = TaskDetailSerializer(many=True, read_only=True)
    comments = SpecCommentSerializer(many=True, read_only=True)
    board_name = serializers.CharField(source="board.name", read_only=True)
    cost_summary = serializers.SerializerMethodField()

    class Meta:
        model = Spec
        fields = [
            "id", "odin_id", "title", "source", "content", "abandoned",
            "board_id", "board_name", "metadata", "created_at", "tasks",
            "comments", "cost_summary",
        ]
        read_only_fields = ["id", "created_at"]

    def get_cost_summary(self, obj):
        from .pricing import compute_spec_cost_summary
        return compute_spec_cost_summary(obj.tasks.all())


class ReflectionRequestSerializer(serializers.Serializer):
    reviewer_agent = serializers.CharField(default="claude")
    reviewer_model = serializers.CharField(default="claude-opus-4-6")
    custom_prompt = serializers.CharField(required=False, allow_blank=True, default="")
    context_selections = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=["description", "comments", "execution_result", "dependencies", "metadata"],
    )
    requested_by = serializers.EmailField(required=False, default="")


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
