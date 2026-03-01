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


class Board(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(default="", blank=True)
    is_trial = models.BooleanField(default=False)
    working_dir = models.CharField(max_length=1024, null=True, blank=True, unique=True)
    odin_initialized = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "boards"

    def __str__(self):
        return self.name


class Label(models.Model):
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


class Spec(models.Model):
    """Odin spec archive — groups related tasks under a planning unit."""

    odin_id = models.CharField(max_length=64, unique=True, db_index=True)
    title = models.CharField(max_length=255)
    source = models.CharField(max_length=255, default="inline")
    content = models.TextField(blank=True, default="")
    abandoned = models.BooleanField(default=False)
    board = models.ForeignKey(Board, on_delete=models.CASCADE, related_name="specs")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

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

    class Meta:
        db_table = "tasks"

    def __str__(self):
        return self.title


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


class TaskComment(models.Model):
    """Deliberate message attached to a task by an agent or human."""

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="comments")
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


class TaskHistory(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="history")
    field_name = models.CharField(max_length=255)
    old_value = models.TextField(default="", blank=True)
    new_value = models.TextField(default="", blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)
    changed_by = models.EmailField()

    class Meta:
        db_table = "task_history"
        ordering = ["-changed_at"]

    def __str__(self):
        return f"{self.task_id}:{self.field_name}"
