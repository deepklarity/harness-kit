from django.db import models
from django.conf import settings


class UserRole(models.TextChoices):
    HUMAN = "HUMAN"
    AGENT = "AGENT"
    ADMIN = "ADMIN"


class User(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    auth_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="taskit_user",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    color = models.CharField(max_length=50, default="#6366f1", blank=True)
    is_admin = models.BooleanField(default=False)
    role = models.CharField(
        max_length=20,
        choices=UserRole.choices,
        default=UserRole.HUMAN,
        db_index=True,
    )
    firebase_uid = models.CharField(max_length=128, unique=True, null=True, blank=True)
    must_change_password = models.BooleanField(default=False)
    password_changed_at = models.DateTimeField(null=True, blank=True)
    available_models = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text=(
            "False for agent Users retired from agent_models.json (seedmodels "
            "prune). The User record is preserved so historical tasks/comments "
            "still resolve FK references; routing/UI queries filter on this."
        ),
    )

    # Agent-only metadata (meaningful when role=AGENT, populated by seedmodels)
    cost_tier = models.CharField(max_length=20, default="medium", blank=True)
    capabilities = models.JSONField(default=list, blank=True)
    cli_command = models.CharField(max_length=255, null=True, blank=True)
    default_model = models.CharField(max_length=255, null=True, blank=True)
    premium_model = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = "users"

    def __str__(self):
        return self.email

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def save(self, *args, **kwargs):
        if self.is_admin:
            self.role = UserRole.ADMIN
        elif not self.role:
            if self.email and self.email.lower().endswith("@odin.agent"):
                self.role = UserRole.AGENT
            else:
                self.role = UserRole.HUMAN
        super().save(*args, **kwargs)


class UserSetting(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="settings")
    preferred_ide_id = models.CharField(max_length=50, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_settings"

    def __str__(self):
        return f"settings:{self.user_id}"


class Board(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(default="", blank=True)
    is_trial = models.BooleanField(default=False)
    working_dir = models.CharField(max_length=1024, null=True, blank=True, unique=True)
    timezone = models.CharField(max_length=64, default="UTC")
    odin_initialized = models.BooleanField(default=False)
    skip_reflection = models.BooleanField(default=False)
    skip_proof = models.BooleanField(default=False)
    auto_start_planned_tasks = models.BooleanField(default=False)
    reflection_model = models.CharField(max_length=255, null=True, blank=True)
    reflection_review_strategy = models.JSONField(
        blank=True, default=dict,
        help_text=(
            "W3.18 size-bucketed reviewer selection. Empty/unset preserves "
            "the single-reviewer default. Set to a dict of {small, medium, "
            "large} → model name to scale reviewer choice by review context "
            "size, e.g. {'small': 'claude-haiku-4-5', 'medium': "
            "'claude-sonnet-4-6', 'large': 'claude-sonnet-4-6'}. "
            "Optional 'thresholds' key overrides the default bucket sizes "
            "(small_max, large_min)."
        ),
    )
    model_escalation_priority = models.JSONField(default=list, blank=True)
    reviewer_order = models.JSONField(
        default=list, blank=True,
        help_text=(
            "Deterministic, quota-aware reflection reviewer walk. Ordered "
            "list of {agent_name, model_name}; index 0 is tried first. "
            "Replaces the old random default-reviewer pick. Empty list "
            "falls back to a deterministic strongest-first ordering "
            "derived from the board's enabled agents."
        ),
    )
    escalation_enabled = models.BooleanField(default=True)
    failure_max_retries = models.IntegerField(default=3)
    routing_policy = models.JSONField(
        default=dict, blank=True,
        help_text=(
            "Per-board routing policy (task #328). The single, editable "
            "override layer over the built-in per-failure-class routing "
            "table (tasks.failure_policy.DEFAULT_POLICY_TABLE). Empty dict "
            "(default) means 'use the built-in defaults' — Default First. "
            "Recognised keys: 'preference_order' (ordered agent names used "
            "when routing a protocol/infra failure to a same-tier peer — "
            "e.g. ['glm','minimax','agy','codex','claude']); "
            "'failure_actions' (per-failure-class {action, max_retries} "
            "overrides); 'capability_escalate_after' (int: number of review "
            "rejections before a deliberate one-tier escalation). Changes "
            "take effect on the next dispatch with no restart."
        ),
    )
    allow_project_root_execution = models.BooleanField(
        default=False,
        help_text=(
            "Opt-in: if True, poll_and_execute will run an agent against the "
            "board's working_dir when no worktree is available. Default is "
            "False — tasks without a worktree are FAILED with a clear reason "
            "instead of silently dispatching into the project root."
        ),
    )
    # Free-form JSON bag for board-level feature flags that don't yet
    # warrant a dedicated column.  Default for every key is "feature on"
    # — Default First.  (The ``auto_promote_enabled`` flag and the
    # promote-check gate were retired in W5 / task #205 — TESTING now
    # means "merged, as good as done", and DONE is a manual flip.)
    metadata = models.JSONField(
        default=dict, blank=True,
        help_text=(
            "Board-level feature flags. Wave 4: "
            "{\"auto_promote_enabled\": false} disables auto-promotion "
            "for this board. Absent or true (default) keeps the feature on."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "boards"

    def __str__(self):
        return self.name


class Label(models.Model):
    board = models.ForeignKey('Board', on_delete=models.CASCADE, related_name="labels", null=True, blank=True)
    name = models.CharField(max_length=255)
    color = models.CharField(max_length=50, default="", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "labels"

    def __str__(self):
        return self.name


class TaskPriority(models.TextChoices):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class TaskStatus(models.TextChoices):
    BACKLOG = "BACKLOG"
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    EXECUTING = "EXECUTING"
    REVIEW = "REVIEW"
    TESTING = "TESTING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class Spec(models.Model):
    """Odin spec archive — groups related tasks under a planning unit."""

    STATUS_PLANNING = "planning"
    STATUS_PLANNING_COMPLETE = "planning_complete"
    STATUS_PLANNING_FAILED = "planning_failed"
    STATUS_ACTIVE = "active"
    STATUS_CHOICES = [
        (STATUS_PLANNING, "Planning"),
        (STATUS_PLANNING_COMPLETE, "Planning Complete"),
        (STATUS_PLANNING_FAILED, "Planning Failed"),
        (STATUS_ACTIVE, "Active"),
    ]

    odin_id = models.CharField(max_length=64, unique=True, db_index=True)
    title = models.CharField(max_length=255)
    source = models.CharField(max_length=255, default="inline")
    content = models.TextField(blank=True, default="")
    abandoned = models.BooleanField(default=False)
    board = models.ForeignKey(Board, on_delete=models.CASCADE, related_name="specs")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    planner_config = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "specs"

    def __str__(self):
        return f"{self.odin_id}: {self.title}"


class Task(models.Model):
    board = models.ForeignKey(Board, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=255)
    description = models.TextField(default="", blank=True)
    dev_eta_seconds = models.BigIntegerField(null=True, blank=True)
    assignee = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks"
    )
    priority = models.CharField(
        max_length=20, choices=TaskPriority.choices, default=TaskPriority.MEDIUM
    )
    status = models.CharField(
        max_length=20, choices=TaskStatus.choices, default=TaskStatus.TODO
    )
    kanban_position = models.IntegerField(default=0, db_index=True)
    created_by = models.EmailField()
    labels = models.ManyToManyField(Label, blank=True, related_name="tasks")
    created_at = models.DateTimeField(auto_now_add=True)
    last_updated_at = models.DateTimeField(auto_now=True)

    # Odin integration fields
    spec = models.ForeignKey(Spec, on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks")
    depends_on = models.JSONField(default=list, blank=True)
    complexity = models.CharField(max_length=20, null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    model_name = models.CharField(max_length=255, null=True, blank=True)
    skip_reflection = models.BooleanField(default=False)
    schedule = models.ForeignKey(
        "TaskSchedule", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="materialized_tasks",
    )
    current_schedule_run = models.ForeignKey(
        "TaskScheduleRun", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="active_tasks",
    )

    class Meta:
        db_table = "tasks"

    def __str__(self):
        return self.title


class ScheduleKind(models.TextChoices):
    ONE_TIME = "ONE_TIME"
    RECURRING = "RECURRING"


class ScheduleStatus(models.TextChoices):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    CANCELED = "CANCELED"
    COMPLETED = "COMPLETED"


class ScheduleRunStatus(models.TextChoices):
    PENDING_RELEASE = "PENDING_RELEASE"
    RELEASED = "RELEASED"
    SKIPPED_OVERLAP = "SKIPPED_OVERLAP"
    COMPLETED_SUCCESS = "COMPLETED_SUCCESS"
    COMPLETED_FAILED = "COMPLETED_FAILED"
    CANCELED = "CANCELED"


class TaskSchedule(models.Model):
    board = models.ForeignKey(Board, on_delete=models.CASCADE, related_name="schedules")
    kind = models.CharField(max_length=20, choices=ScheduleKind.choices)
    status = models.CharField(max_length=20, choices=ScheduleStatus.choices, default=ScheduleStatus.ACTIVE)
    timezone = models.CharField(max_length=64)
    template_title = models.CharField(max_length=255)
    template_description = models.TextField(default="", blank=True)
    template_priority = models.CharField(
        max_length=20, choices=TaskPriority.choices, default=TaskPriority.MEDIUM,
    )
    template_assignee = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="scheduled_tasks",
    )
    template_model_name = models.CharField(max_length=255, null=True, blank=True)
    template_label_ids = models.JSONField(default=list, blank=True)
    template_depends_on = models.JSONField(default=list, blank=True)
    template_dev_eta_seconds = models.BigIntegerField(null=True, blank=True)
    template_spec = models.ForeignKey(
        Spec, on_delete=models.SET_NULL, null=True, blank=True, related_name="schedules",
    )
    template_metadata = models.JSONField(default=dict, blank=True)
    starts_at_local = models.DateTimeField()
    starts_at_utc = models.DateTimeField()
    next_run_at_utc = models.DateTimeField(null=True, blank=True, db_index=True)
    recurrence_rule = models.JSONField(default=dict, blank=True)
    materialized_task = models.ForeignKey(
        Task, on_delete=models.SET_NULL, null=True, blank=True, related_name="origin_schedule",
    )
    last_released_run = models.ForeignKey(
        "TaskScheduleRun", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    paused_at = models.DateTimeField(null=True, blank=True)
    canceled_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "task_schedules"
        ordering = ["next_run_at_utc", "id"]

    def __str__(self):
        return f"Schedule {self.id} ({self.kind})"


class TaskScheduleRun(models.Model):
    schedule = models.ForeignKey(TaskSchedule, on_delete=models.CASCADE, related_name="runs")
    run_number = models.IntegerField(default=1)
    task = models.ForeignKey(Task, on_delete=models.SET_NULL, null=True, blank=True, related_name="schedule_runs")
    scheduled_for_utc = models.DateTimeField(db_index=True)
    released_at_utc = models.DateTimeField(null=True, blank=True)
    finished_at_utc = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=32, choices=ScheduleRunStatus.choices, default=ScheduleRunStatus.PENDING_RELEASE)
    terminal_task_status = models.CharField(max_length=20, choices=TaskStatus.choices, null=True, blank=True)
    release_reason = models.TextField(blank=True, default="")
    result_summary = models.TextField(blank=True, default="")
    template_snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "task_schedule_runs"
        ordering = ["-scheduled_for_utc", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["schedule", "scheduled_for_utc"],
                name="uniq_schedule_occurrence",
            ),
        ]

    def __str__(self):
        return f"ScheduleRun {self.id} for schedule {self.schedule_id}"


class BoardMembership(models.Model):
    board = models.ForeignKey(Board, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="board_memberships")
    disabled_models = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "board_memberships"
        unique_together = [("board", "user")]

    def __str__(self):
        return f"{self.board_id}:{self.user_id}"


class CommentType(models.TextChoices):
    STATUS_UPDATE = "status_update"
    QUESTION = "question"
    REPLY = "reply"
    PROOF = "proof"
    SUMMARY = "summary"
    REFLECTION = "reflection"
    PLANNING = "planning"
    PROMOTION_REPORT = "promotion_report"


class TaskComment(models.Model):
    """Deliberate message attached to a task by an agent or human."""

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="comments")
    schedule_run = models.ForeignKey(
        "TaskScheduleRun", on_delete=models.SET_NULL, null=True, blank=True, related_name="comments",
    )
    author_email = models.EmailField()
    author_label = models.CharField(max_length=255, blank=True)
    content = models.TextField()
    attachments = models.JSONField(default=list, blank=True)
    comment_type = models.CharField(
        max_length=20,
        choices=CommentType.choices,
        default=CommentType.STATUS_UPDATE,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "task_comments"
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.task_id}:{self.author_email}"


class CommentAttachment(models.Model):
    """File uploaded as proof evidence for a task comment."""

    comment = models.ForeignKey(
        TaskComment, on_delete=models.CASCADE,
        related_name="file_attachments", null=True, blank=True,
    )
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to="screenshots/%Y/%m/")
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100, default="application/octet-stream")
    file_size = models.BigIntegerField(default=0)
    uploaded_by = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "comment_attachments"

    def __str__(self):
        return f"{self.task_id}:{self.original_filename}"


class SpecComment(models.Model):
    """Deliberate message attached to a spec by an agent or human."""

    spec = models.ForeignKey(Spec, on_delete=models.CASCADE, related_name="comments")
    author_email = models.EmailField()
    author_label = models.CharField(max_length=255, blank=True)
    content = models.TextField()
    attachments = models.JSONField(default=list, blank=True)
    comment_type = models.CharField(
        max_length=20,
        choices=CommentType.choices,
        default=CommentType.STATUS_UPDATE,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "spec_comments"
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.spec_id}:{self.author_email}"


class ReflectionStatus(models.TextChoices):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ReflectionReport(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="reflections")

    # Request params (set at creation)
    reviewer_agent = models.CharField(max_length=50)
    reviewer_model = models.CharField(max_length=100)
    custom_prompt = models.TextField(blank=True, default="")
    context_selections = models.JSONField(default=list)
    requested_by = models.EmailField()

    # Execution state
    status = models.CharField(max_length=20, choices=ReflectionStatus.choices, default=ReflectionStatus.PENDING)

    # Report content (populated by Odin after completion)
    quality_assessment = models.TextField(blank=True, default="")
    slop_detection = models.TextField(blank=True, default="")
    improvements = models.TextField(blank=True, default="")
    agent_optimization = models.TextField(blank=True, default="")
    quota_failure = models.TextField(blank=True, default="")
    verdict = models.CharField(max_length=20, blank=True, default="")
    verdict_summary = models.TextField(blank=True, default="")
    # W3.18 — why this reviewer was picked. One of:
    #   size_small | size_medium | size_large   (board strategy matched a bucket)
    #   board_model_override                     (board.reflection_model set)
    #   caller_override                          (manual reflect() with explicit model)
    #   forced_provider                          (env-var forced provider)
    #   default                                  (no strategy / strategy had no bucket)
    selection_reason = models.CharField(max_length=64, blank=True, default="")
    raw_output = models.TextField(blank=True, default="")
    execution_trace = models.TextField(blank=True, default="")
    assembled_prompt = models.TextField(blank=True, default="")

    # Metadata
    duration_ms = models.BigIntegerField(null=True, blank=True)
    token_usage = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "reflection_reports"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Reflection {self.id} on Task {self.task_id} ({self.status})"

    def reviewer_identity_email(self):
        """The {agent}+{model}@odin.agent identity email for whoever ran this
        reflection — the author of its verdict comment.

        ``requested_by`` is who *asked* for the reflection (an operator or
        ``system@taskit``), not who wrote the verdict. The verdict is the
        reviewer's, so its comment must be attributed to the reviewer using
        the same ``{agent}+{model}@odin.agent`` convention agent comments use.
        Falls back to a system identity only when reviewer fields are blank.
        """
        agent = (self.reviewer_agent or "").strip()
        model = (self.reviewer_model or "").strip()
        if agent and model:
            return f"{agent}+{model}@odin.agent"
        return "system@odin.agent"


class TaskRunState(models.TextChoices):
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"
    EXPIRED = "EXPIRED"
    KILLED = "KILLED"


class TaskRun(models.Model):
    """A single sandbox-execution attempt for a task (task #210).

    Created by the executor at spawn time (dag_executor.poll_and_execute /
    execution.local.LocalOdinStrategy.trigger) and heartbeated from the
    existing subprocess-monitoring loop — one mechanism, not two. This is
    the fencing anchor for writes at the API boundary: a write whose
    run_token doesn't match the task's current RUNNING row is rejected
    (see views.execution_result). Legacy metadata.active_execution blobs
    stay for compatibility this wave; TaskRun is the source of truth
    going forward.
    """

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="runs")
    spec = models.ForeignKey(Spec, on_delete=models.SET_NULL, null=True, blank=True, related_name="runs")
    run_token = models.CharField(max_length=64, unique=True, db_index=True)
    pid = models.IntegerField(null=True, blank=True)
    sandbox_name = models.CharField(max_length=255, blank=True, default="")
    state = models.CharField(
        max_length=20, choices=TaskRunState.choices, default=TaskRunState.RUNNING, db_index=True,
    )

    started_at = models.DateTimeField(auto_now_add=True)
    last_heartbeat = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "task_runs"
        ordering = ["-started_at"]

    def __str__(self):
        return f"TaskRun {self.run_token[:8]} (task {self.task_id}, {self.state})"


class MergeTrigger(models.TextChoices):
    REFLECTION_PASS = "reflection_pass"
    RETRY = "retry"
    HUMAN_RESUME = "human_resume"


class MergeMode(models.TextChoices):
    STATIC = "static"
    AGENT = "agent"
    HUMAN_ASSISTED = "human_assisted"


class MergeOutcome(models.TextChoices):
    MERGED = "merged"
    CONFLICT = "conflict"
    ERROR = "error"


class MergeAttempt(models.Model):
    """One rung of the merge ladder for a task (task #209).

    The ladder is: static git merge first (cheap, no model) -> merge agent
    only on conflict (model/tokens/cost) -> human only when the agent can't
    resolve. Every rung leaves one of these rows so a spec's merge story is
    queryable the same way reflections already are (see spec_trace.py's
    merges section). Cost is derived via pricing.estimate_task_cost from
    agent_model + token_usage, never stored — same contract as
    ReflectionReport.
    """

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="merge_attempts")
    spec = models.ForeignKey(Spec, on_delete=models.SET_NULL, null=True, blank=True, related_name="merge_attempts")

    trigger = models.CharField(max_length=20, choices=MergeTrigger.choices)
    mode = models.CharField(max_length=20, choices=MergeMode.choices)
    outcome = models.CharField(max_length=20, choices=MergeOutcome.choices)

    dispatched_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField()

    conflicting_files = models.JSONField(default=list, blank=True)

    agent_model = models.CharField(max_length=100, blank=True, default="")
    token_usage = models.JSONField(default=dict, blank=True)

    error_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "merge_attempts"
        ordering = ["-started_at"]

    def __str__(self):
        return f"MergeAttempt {self.id} (task {self.task_id}, {self.mode}/{self.outcome})"


class MistakeEntry(models.Model):
    """One line in the mistakes ledger (task #223).

    Distills a reflection NEEDS_WORK/FAIL verdict or a task-FAILED
    transition into a single queryable line so a future similar task can
    carry the warning via the wave-5 twins comment. Rule-based — no extra
    model call: the one-liner is distilled from the structured verdict /
    failure metadata, and ``failure_class`` is reused from
    ``failure_tagger`` where a known signature matches.

    Dedup is per (task, source, source_id): one entry per reflection
    report or per execution run, so re-processing the same event never
    double-records (see ``tasks.mistakes.record_*``).
    """

    SOURCE_REFLECTION = "reflection"
    SOURCE_EXECUTION = "execution"

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="mistakes")
    spec = models.ForeignKey(
        Spec, on_delete=models.SET_NULL, null=True, blank=True, related_name="mistakes",
    )
    source = models.CharField(max_length=20)
    # For reflections: the reflection report id. For executions: the run_token.
    source_id = models.CharField(max_length=64, blank=True, default="")
    agent = models.CharField(max_length=255, blank=True, default="")
    model = models.CharField(max_length=255, blank=True, default="")
    one_liner = models.CharField(max_length=500)
    failure_class = models.CharField(max_length=40, blank=True, default="")
    verdict = models.CharField(max_length=20, blank=True, default="")
    # Failure fingerprint (W6.3 / task #225): normalized
    # ``<salient> / <provider> / <stage>`` shape, indexed for fast lookup
    # when a new occurrence needs to ask "how many times have we seen
    # this?".  Empty for legacy rows (no backfill needed — the lookup
    # script can rebuild on demand, see ``tasks.fingerprints``).
    fingerprint = models.CharField(
        max_length=200, blank=True, default="", db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mistake_entries"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Mistake {self.id} (task {self.task_id}, {self.source})"


class ErrorEvent(models.Model):
    """One row in the error ledger (task #222).

    Captures every error the system already handles/logs — failure_tagger
    misses, merge ladder failures, reflection ERROR verdicts, spec-verify
    gate crashes, celery task exceptions — into a structured store so an
    operator can triage them in one place instead of grepping logs.

    Distinct from MistakeEntry (W6.1) by design: MistakeEntry is the
    per-task contract that drives twins warnings (one row per reflection
    report or execution run, ``task`` is required, no disposition
    lifecycle). ErrorEvent is the system-wide triage surface where a
    celery worker crash before any task exists, or an unknown
    failure-class miss, still belongs — and where every entry carries a
    disposition so the operator can mark it fixed or non-issue from the
    API. Reusing MistakeEntry would have required loosening its
    not-null task FK and adding a disposition field that the twins
    path never reads; a separate model keeps each contract clean.

    Dedup key: ``(source, source_id)`` when ``source_id`` is non-empty
    (re-processing the same event never double-records).  The
    ``symptom_signature`` field groups visually-identical symptoms for
    the brief view, regardless of source_id.
    """

    SOURCE_FAILURE_TAGGER = "failure_tagger"
    SOURCE_MERGE_FAILURE = "merge_failure"
    SOURCE_REFLECTION_ERROR = "reflection_error"
    SOURCE_GATE_CRASH = "gate_crash"
    SOURCE_CELERY_EXCEPTION = "celery_exception"
    SOURCE_AGENT_MALFORMED_STATUS = "agent_malformed_status"
    SOURCE_REFLECTION_NO_REVIEWER = "reflection_no_reviewer"
    SOURCE_COMMENT_ATTRIBUTION_LOSS = "comment_attribution_loss"
    SOURCE_REFLECT_PARAM_IGNORED = "reflect_param_ignored"
    SOURCE_ERROR_LOOP = "error_loop"

    SOURCE_CHOICES = (
        (SOURCE_FAILURE_TAGGER, "Failure tagger miss"),
        (SOURCE_MERGE_FAILURE, "Merge ladder failure"),
        (SOURCE_REFLECTION_ERROR, "Reflection ERROR verdict"),
        (SOURCE_GATE_CRASH, "Spec-verify gate crash"),
        (SOURCE_CELERY_EXCEPTION, "Celery task exception"),
        (SOURCE_AGENT_MALFORMED_STATUS, "Agent emitted malformed ODIN-STATUS block"),
        (SOURCE_REFLECTION_NO_REVIEWER, "Reflection no reviewer available"),
        (SOURCE_COMMENT_ATTRIBUTION_LOSS, "Comment attributed to unknown@user (attribution lost)"),
        (SOURCE_REFLECT_PARAM_IGNORED, "Reflect endpoint ignored explicit reviewer"),
        (SOURCE_ERROR_LOOP, "Run reconciler error-loop detection"),
    )

    DISPOSITION_OPEN = "open"
    DISPOSITION_FIXED = "fixed"
    DISPOSITION_NON_ISSUE = "non-issue"

    DISPOSITION_CHOICES = (
        (DISPOSITION_OPEN, "Open"),
        (DISPOSITION_FIXED, "Fixed"),
        (DISPOSITION_NON_ISSUE, "Non-issue"),
    )

    source = models.CharField(max_length=30, choices=SOURCE_CHOICES)
    source_id = models.CharField(max_length=64, blank=True, default="")
    symptom = models.CharField(max_length=500)
    symptom_signature = models.CharField(max_length=200, db_index=True)
    failure_class = models.CharField(max_length=40, blank=True, default="")
    task = models.ForeignKey(
        Task, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="error_events",
    )
    spec = models.ForeignKey(
        Spec, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="error_events",
    )
    log_path = models.CharField(max_length=500, blank=True, default="")
    log_tail = models.TextField(blank=True, default="")
    context = models.JSONField(default=dict, blank=True)
    disposition = models.CharField(
        max_length=20, choices=DISPOSITION_CHOICES,
        default=DISPOSITION_OPEN, db_index=True,
    )
    disposition_note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "error_events"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"ErrorEvent {self.id} ({self.source}/{self.disposition})"


class TaskHistory(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="history")
    schedule_run = models.ForeignKey(
        "TaskScheduleRun", on_delete=models.SET_NULL, null=True, blank=True, related_name="history_entries",
    )
    field_name = models.CharField(max_length=255)
    old_value = models.TextField(default="", blank=True)
    new_value = models.TextField(default="", blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)
    changed_by = models.EmailField()

    # Optional per-row metadata bag (e.g. {"reason": "rework_continuity"}).
    # Tolerant accessor: `latest_assignee.metadata or {}` is the contract
    # callers use to read row-level context without an extra migration.
    # Implementation note: this is a Python property, not a column — rows
    # without an explicit setter never raise AttributeError on read.
    @property
    def metadata(self):
        return getattr(self, "_row_metadata", {}) or {}

    @metadata.setter
    def metadata(self, value):
        self._row_metadata = value or {}

    class Meta:
        db_table = "task_history"
        ordering = ["-changed_at"]

    def __str__(self):
        return f"{self.task_id}:{self.field_name}"


class NotificationType(models.TextChoices):
    TASK_ASSIGNED = "task_assigned"
    COMMENT_ADDED = "comment_added"
    STATUS_CHANGED = "status_changed"
    PLANNING_COMPLETE = "planning_complete"
    QUESTION_ASKED = "question_asked"
    SPEC_FINISHED = "spec_finished"
    TASK_FAILED = "task_failed"
    TASK_FAILED_REMINDER = "task_failed_reminder"


class Notification(models.Model):
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    notification_type = models.CharField(max_length=30, choices=NotificationType.choices, db_index=True)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True, default="")
    task = models.ForeignKey(Task, on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    spec = models.ForeignKey(Spec, on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    board = models.ForeignKey(Board, on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    actor_email = models.EmailField(blank=True, default="")
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "notifications"
        indexes = [
            models.Index(fields=["recipient", "is_read", "-created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.recipient_id}:{self.notification_type}:{self.title[:40]}"


class NotificationPreference(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="notification_preference")
    desktop_enabled = models.BooleanField(default=True)
    sound_enabled = models.BooleanField(default=True)
    disabled_types = models.JSONField(default=list, blank=True)
    quiet_hours_start = models.CharField(max_length=5, null=True, blank=True)
    quiet_hours_end = models.CharField(max_length=5, null=True, blank=True)
    quiet_hours_timezone = models.CharField(max_length=50, default="UTC")

    class Meta:
        db_table = "notification_preferences"

    def __str__(self):
        return f"NotificationPreference({self.user_id})"


class DataOpMarker(models.Model):
    """Done-marker for an idempotent data operation (task #247).

    Schema migrations have Django; *data* operations shipped by agents had
    nothing — they ran in a sandbox that can't touch the prod DB, and no
    lifecycle re-ran them after merge. The dataops registry fixes that:
    each op registers itself in ``tasks/dataops.py`` and a ``post_migrate``
    hook runs any op without a marker here, so a service restart (which
    runs ``migrate``) is all a shipped data op needs.

    The marker is bookkeeping, not the guard. Every registered op must be
    idempotent on its own — safe to re-run — so a wiped marker or a
    re-import never corrupts data; it just redoes work.
    """

    name = models.CharField(max_length=128, unique=True)
    ran_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "dataop_markers"
        ordering = ["-ran_at"]

    def __str__(self):
        return f"DataOpMarker({self.name})"


class SystemSetting(models.Model):
    """System-wide settings persisted in the database.

    Each setting is identified by a unique key and stores an integer value.
    Settings are read per poll cycle, allowing runtime changes without restart.
    """

    key = models.CharField(max_length=255, unique=True, primary_key=True)
    value = models.IntegerField()
    help_text = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "system_settings"

    def __str__(self):
        return f"SystemSetting({self.key}={self.value})"

    def get_suggested_max(self):
        """Calculate suggested max concurrency based on host RAM.

        Roughly 4 GB per sandbox session. Returns at least 1.
        """
        import os
        try:
            total_ram_bytes = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
            total_ram_gb = total_ram_bytes / (1024 ** 3)
            suggested = int(total_ram_gb / 4)
            return max(1, suggested)
        except (AttributeError, OSError):
            return 1
