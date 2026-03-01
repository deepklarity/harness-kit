"""Disk storage layer for Taskit tasks."""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from odin.taskit.models import Task


class TaskStore:
    """Stores tasks as JSON files in a directory."""

    def __init__(self, storage_dir: str):
        self._dir = Path(storage_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._dir / "index.json"

    def save(self, task: Task) -> None:
        task.updated_at = datetime.now()
        path = self._dir / f"task_{task.id}.json"
        path.write_text(task.model_dump_json(indent=2))
        self._update_index(task)

    def load(self, task_id: str) -> Optional[Task]:
        path = self._dir / f"task_{task_id}.json"
        if not path.exists():
            return None
        return Task.model_validate_json(path.read_text())

    def load_all(self) -> List[Task]:
        tasks = []
        for path in sorted(self._dir.glob("task_*.json")):
            try:
                tasks.append(Task.model_validate_json(path.read_text()))
            except Exception:
                continue
        return tasks

    def delete(self, task_id: str) -> bool:
        path = self._dir / f"task_{task_id}.json"
        if path.exists():
            path.unlink()
            self._remove_from_index(task_id)
            return True
        return False

    def _update_index(self, task: Task) -> None:
        index = self._load_index()
        index[task.id] = {
            "title": task.title,
            "status": task.status.value,
            "assigned_agent": task.assigned_agent,
            "spec_id": task.spec_id,
        }
        self._save_index(index)

    def _remove_from_index(self, task_id: str) -> None:
        index = self._load_index()
        index.pop(task_id, None)
        self._save_index(index)

    def _load_index(self) -> Dict:
        if self._index_path.exists():
            return json.loads(self._index_path.read_text())
        return {}

    def _save_index(self, index: Dict) -> None:
        self._index_path.write_text(json.dumps(index, indent=2))
