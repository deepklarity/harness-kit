"""Structured JSON logger for orchestration runs."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


class OdinLogger:
    """Appends structured JSON log entries to a per-run JSONL file."""

    def __init__(self, log_dir: str):
        self._dir = Path(log_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._run_id = f"run_{ts}"
        self._path = self._dir / f"{self._run_id}.jsonl"

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def log_path(self) -> Path:
        return self._path

    def log(
        self,
        action: str,
        task_id: Optional[str] = None,
        agent: Optional[str] = None,
        input_prompt: Optional[str] = None,
        output: Optional[str] = None,
        duration_ms: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "run_id": self._run_id,
            "action": action,
            "task_id": task_id,
            "agent": agent,
            "input_prompt": input_prompt,
            "output": output[:2000] if output else None,
            "duration_ms": duration_ms,
            "metadata": metadata,
        }
        # Remove None values for compact logs
        entry = {k: v for k, v in entry.items() if v is not None}
        with open(self._path, "a") as f:
            f.write(json.dumps(entry) + "\n")
