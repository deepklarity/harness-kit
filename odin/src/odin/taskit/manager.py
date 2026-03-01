"""Task CRUD and management."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, List, Optional

from odin.taskit.models import Comment, Task, TaskStatus
from odin.taskit.store import TaskStore

if TYPE_CHECKING:
    from odin.backends.base import BoardBackend


class TaskManager:
    """High-level task operations backed by disk storage or a board backend.

    When a ``backend`` is provided, all storage is delegated to it.
    Otherwise, falls back to the local ``TaskStore`` (original behavior).
    """

    def __init__(self, storage_dir: str, backend: Optional[BoardBackend] = None):
        self._backend = backend
        self._store = TaskStore(storage_dir)

    # -- storage helpers (route through backend when available) --

    def _create(self, task: Task) -> None:
        if self._backend:
            self._backend.create_task(task)
        else:
            self._store.save(task)

    def _update(self, task: Task) -> None:
        if self._backend:
            self._backend.update_task(task)
        else:
            self._store.save(task)

    def _load(self, task_id: str) -> Optional[Task]:
        if self._backend:
            return self._backend.load_task(task_id)
        return self._store.load(task_id)

    def _load_all(self) -> List[Task]:
        if self._backend:
            return self._backend.load_all_tasks()
        return self._store.load_all()

    def _delete(self, task_id: str) -> bool:
        if self._backend:
            return self._backend.delete_task(task_id)
        return self._store.delete(task_id)

    # -- public API (unchanged signatures) --

    def create_task(
        self,
        title: str,
        description: str,
        parent_task_id: Optional[str] = None,
        metadata: Optional[dict] = None,
        spec_id: Optional[str] = None,
    ) -> Task:
        task = Task(
            id=uuid.uuid4().hex[:12],
            title=title,
            description=description,
            parent_task_id=parent_task_id,
            metadata=metadata or {},
            spec_id=spec_id,
        )
        self._create(task)
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        return self._load(task_id)

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        agent: Optional[str] = None,
        parent_id: Optional[str] = None,
        spec_id: Optional[str] = None,
    ) -> List[Task]:
        tasks = self._load_all()
        if status:
            tasks = [t for t in tasks if t.status == status]
        if agent:
            tasks = [t for t in tasks if t.assigned_agent == agent]
        if parent_id:
            tasks = [t for t in tasks if t.parent_task_id == parent_id]
        if spec_id:
            tasks = [t for t in tasks if t.spec_id == spec_id]
        return tasks

    def assign_task(self, task_id: str, agent: str) -> Optional[Task]:
        task = self._load(task_id)
        if not task:
            return None
        task.assigned_agent = agent
        task.status = TaskStatus.TODO
        self._update(task)
        return task

    def update_status(
        self, task_id: str, status: TaskStatus
    ) -> Optional[Task]:
        task = self._load(task_id)
        if not task:
            return None
        task.status = status
        self._update(task)
        return task

    def update_task(self, task: Task) -> None:
        """Persist an already-loaded task (e.g. after metadata updates)."""
        self._update(task)

    def save_task(self, task: Task) -> None:
        """Persist an already-loaded task (e.g. after metadata updates).

        Deprecated: prefer update_task() for explicit intent. This method
        remains for backwards compatibility.
        """
        self._update(task)

    def add_comment(
        self,
        task_id: str,
        author: str,
        content: str,
        attachments: Optional[List[str]] = None,
        model_name: Optional[str] = None,
        comment_type: Optional[str] = None,
    ) -> Optional[Task]:
        # Route through backend's dedicated comment endpoint when available
        if self._backend:
            author_email = self._format_actor_email(author, model_name)
            author_label = self._format_actor_label(author, model_name)
            self._backend.add_comment(
                task_id=task_id,
                author_email=author_email,
                content=content,
                author_label=author_label,
                attachments=attachments,
                comment_type=comment_type,
            )
            return self._load(task_id)

        # Local disk fallback — append to task's comment list
        task = self._load(task_id)
        if not task:
            return None
        comment = Comment(
            author=author,
            content=content,
            attachments=attachments or [],
        )
        task.comments.append(comment)
        self._update(task)
        return task

    @staticmethod
    def _format_actor_email(agent: str, model_name: Optional[str] = None) -> str:
        """Format agent+model into an email identity.

        Examples:
          ("minimax", "MiniMax-M2.5") -> "minimax+MiniMax-M2.5@odin.agent"
          ("odin", None) -> "odin@harness.kit"
        """
        if agent == "odin" and not model_name:
            return "odin@harness.kit"
        if model_name:
            return f"{agent}+{model_name}@odin.agent"
        return f"{agent}@odin.agent"

    @staticmethod
    def _format_actor_label(agent: str, model_name: Optional[str] = None) -> str:
        """Format a human-readable actor label.

        Examples:
          ("minimax", "MiniMax-M2.5") -> "minimax (MiniMax-M2.5)"
          ("odin", None) -> "odin"
        """
        if model_name:
            return f"{agent} ({model_name})"
        return agent

    def get_comments(self, task_id: str) -> list:
        """Fetch comments for a task.

        Backend returns list of comment dicts. Local disk returns
        the task's Comment objects (via task.comments).
        """
        if self._backend:
            return self._backend.get_comments(task_id)
        # Local disk fallback: return from task model
        task = self._load(task_id)
        if not task:
            return []
        return [
            {"content": c.content, "author": c.author, "attachments": c.attachments}
            for c in task.comments
        ]

    def record_execution_result(
        self,
        task_id: str,
        execution_result: dict,
        status: "TaskStatus",
        actor_email: str,
    ) -> Optional[Task]:
        """Record a complete execution result.

        For backends that support it (TaskIt REST), delegates the entire payload.
        For local disk, processes the raw output locally and stores comment + status.
        """
        if self._backend:
            self._backend.record_execution_result(
                task_id=task_id,
                execution_result=execution_result,
                status=status.value.upper(),
                actor_email=actor_email,
            )
            return self._load(task_id)

        # Local disk fallback: process here
        from odin.orchestrator import Orchestrator

        task = self._load(task_id)
        if not task:
            return None

        raw_output = execution_result.get("raw_output", "")
        agent_text = Orchestrator._extract_agent_text(raw_output)
        clean_output, parsed_success, summary = Orchestrator._parse_envelope(agent_text)

        success = execution_result.get("success", False)
        if parsed_success is not None:
            success = parsed_success

        task.status = status

        verb = "Completed" if success else "Failed"
        default_summary = "Completed successfully" if success else f"Failed: {execution_result.get('error', 'unknown error')}"

        from odin.models import TaskResult
        result_for_comment = TaskResult(
            success=success,
            output=clean_output,
            error=execution_result.get("error"),
            duration_ms=execution_result.get("duration_ms"),
            agent=execution_result.get("agent"),
            metadata=execution_result.get("metadata", {}),
        )
        comment_text = Orchestrator._compose_comment(verb, result_for_comment, summary or default_summary)

        comment = Comment(
            author=execution_result.get("agent", "unknown"),
            content=comment_text,
        )
        task.comments.append(comment)
        self._update(task)
        return task

    def resolve_task_id(self, prefix: str) -> Optional[str]:
        """Resolve a task ID prefix to a full ID.

        Returns the full ID if exactly one task matches, None otherwise.
        """
        prefix = str(prefix)
        tasks = self._load_all()
        matches = [t.id for t in tasks if t.id.startswith(prefix)]
        if len(matches) == 1:
            return matches[0]
        return None

    def get_ready_tasks(self, task_ids: Optional[List[str]] = None) -> List[Task]:
        """Return ASSIGNED tasks whose dependencies are all COMPLETED.

        Uses the centralized dependency module for consistent checking.
        If task_ids is provided, only consider those tasks.
        If task_ids is None, consider all tasks.
        """
        if task_ids is not None:
            candidates = []
            for tid in task_ids:
                t = self._load(tid)
                if t:
                    candidates.append(t)
        else:
            candidates = self._load_all()

        # Build a resolver that uses pre-loaded tasks first, falls back to _load
        tasks_by_id = {t.id: t for t in candidates}

        def _resolver(task_id: str) -> Optional[Task]:
            if task_id in tasks_by_id:
                return tasks_by_id[task_id]
            # Dep may be outside the candidate set — load it
            t = self._load(task_id)
            if t:
                tasks_by_id[task_id] = t
            return t

        from odin.dependencies import get_ready_tasks as _get_ready
        return _get_ready(candidates, _resolver)

    def get_dependents(self, task_id: str, task_ids: Optional[List[str]] = None) -> List[str]:
        """Return IDs of tasks that depend on the given task_id."""
        if task_ids:
            tasks = [self._load(tid) for tid in task_ids]
            tasks = [t for t in tasks if t]
        else:
            tasks = self._load_all()
        return [t.id for t in tasks if task_id in t.depends_on]

    def delete_task(self, task_id: str) -> bool:
        return self._delete(task_id)
