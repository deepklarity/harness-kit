"""Abstract base class for board backends."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from odin.taskit.models import Task
from odin.specs import SpecArchive


class BoardBackend(ABC):
    """Interface for task/spec storage backends.

    Implementations can target local disk (JSON files), TaskIt (REST API),
    Jira, or any other board system.
    """

    # -- Task operations --

    @abstractmethod
    def create_task(self, task: Task) -> Task:
        """Create a new task. Returns the task with backend-assigned ID."""
        ...

    @abstractmethod
    def update_task(self, task: Task) -> Task:
        """Update an existing task."""
        ...

    def save_task(self, task: Task) -> Task:
        """Persist a task. Routes to create_task or update_task."""
        if not task.id or not task.id.isdigit():
            return self.create_task(task)
        return self.update_task(task)

    def add_comment(
        self,
        task_id: str,
        author_email: str,
        content: str,
        author_label: str = "",
        attachments: Optional[list] = None,
        comment_type: Optional[str] = None,
    ) -> None:
        """Post a comment on a task. Default implementation is a no-op."""
        pass

    def get_comments(self, task_id: str) -> List[Dict]:
        """Fetch comments for a task. Returns list of comment dicts.

        Default implementation returns empty list. Backends that support
        comments (e.g. TaskIt REST) should override.
        """
        return []

    def record_execution_result(
        self,
        task_id: str,
        execution_result: Dict[str, Any],
        status: str,
        actor_email: str,
    ) -> None:
        """Record a complete execution result atomically.

        The backend receives the raw execution payload and handles all
        processing (text extraction, envelope parsing, comment composition).
        Default implementation is a no-op — subclasses should override.
        """
        pass

    @abstractmethod
    def load_task(self, task_id: str) -> Optional[Task]:
        """Load a single task by ID."""
        ...

    @abstractmethod
    def load_all_tasks(self) -> List[Task]:
        """Load all tasks."""
        ...

    @abstractmethod
    def delete_task(self, task_id: str) -> bool:
        """Delete a task by ID. Returns True if deleted."""
        ...

    # -- Spec operations --

    @abstractmethod
    def save_spec(self, spec: SpecArchive) -> SpecArchive:
        """Persist a spec archive. Creates or updates."""
        ...

    @abstractmethod
    def load_spec(self, spec_id: str) -> Optional[SpecArchive]:
        """Load a single spec by ID."""
        ...

    @abstractmethod
    def load_all_specs(self) -> List[SpecArchive]:
        """Load all spec archives."""
        ...

    @abstractmethod
    def set_spec_abandoned(self, spec_id: str) -> bool:
        """Mark a spec as abandoned. Returns True if found."""
        ...

    @abstractmethod
    def delete_spec(self, spec_id: str) -> bool:
        """Delete a spec by ID. Returns True if deleted."""
        ...

    # -- Label operations --

    @abstractmethod
    def list_labels(self) -> List[dict]:
        """List all labels. Returns list of dicts with 'id', 'name', 'color'."""
        ...

    @abstractmethod
    def create_label(self, name: str, color: str) -> dict:
        """Create a label. Returns dict with 'id', 'name', 'color'."""
        ...
