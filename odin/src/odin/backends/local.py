"""Local disk backend — wraps existing TaskStore and SpecStore."""

from typing import List, Optional

from odin.backends.base import BoardBackend
from odin.backends.registry import register_backend
from odin.specs import SpecArchive, SpecStore
from odin.taskit.models import Task
from odin.taskit.store import TaskStore


@register_backend("local")
class LocalBackend(BoardBackend):
    """Board backend backed by local JSON files.

    Wraps the existing TaskStore and SpecStore with zero behavior change.
    """

    def __init__(self, task_storage: str = ".odin/tasks", spec_storage: str = ".odin/specs", **kwargs):
        self._task_store = TaskStore(task_storage)
        self._spec_store = SpecStore(spec_storage)

    # -- Task operations --

    def create_task(self, task: Task) -> Task:
        self._task_store.save(task)
        return task

    def update_task(self, task: Task) -> Task:
        self._task_store.save(task)
        return task

    def load_task(self, task_id: str) -> Optional[Task]:
        return self._task_store.load(task_id)

    def load_all_tasks(self) -> List[Task]:
        return self._task_store.load_all()

    def delete_task(self, task_id: str) -> bool:
        return self._task_store.delete(task_id)

    # -- Spec operations --

    def save_spec(self, spec: SpecArchive) -> SpecArchive:
        self._spec_store.save(spec)
        return spec

    def load_spec(self, spec_id: str) -> Optional[SpecArchive]:
        return self._spec_store.load(spec_id)

    def load_all_specs(self) -> List[SpecArchive]:
        return self._spec_store.load_all()

    def set_spec_abandoned(self, spec_id: str) -> bool:
        result = self._spec_store.set_abandoned(spec_id)
        return result is not None

    def delete_spec(self, spec_id: str) -> bool:
        return self._spec_store.delete(spec_id)

    # -- Label operations --

    def list_labels(self) -> List[dict]:
        return []

    def create_label(self, name: str, color: str) -> dict:
        return {"id": 0, "name": name, "color": color}
