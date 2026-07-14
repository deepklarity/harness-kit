"""Resolve the 'current session' for a task.

A session is a single run of either task execution or reflection that produces
a JSONL trace file. Exactly one kind of session may be active per task at a
time (task execution and reflection do not run concurrently).

Resolution priority:
    1. Any ReflectionReport for this task with status=RUNNING
       → reflection session, live=True
    2. Task status in {IN_PROGRESS, EXECUTING}
       → task execution session, live=True
    3. Most recently modified on-disk JSONL (either type)
       → last run, live=False
    4. No file exists → return None

Trace file locations (absolute, written by odin with cwd=working_dir):
    - Task exec : {working_dir}/.odin/logs/task_{task_id}.trace.jsonl
    - Reflection: {working_dir}/.odin/logs/reflect_{report_id}.trace.jsonl
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from .execution.utils import resolve_working_dir
from .models import ReflectionReport, ReflectionStatus, Task, TaskStatus


SESSION_TYPE_TASK = "task_execution"
SESSION_TYPE_REFLECTION = "reflection"

_TASK_EXECUTING_STATUSES = {TaskStatus.IN_PROGRESS, TaskStatus.EXECUTING}


@dataclass
class SessionInfo:
    session_type: str            # "task_execution" | "reflection"
    jsonl_path: str              # absolute path
    live: bool                   # True if the run is currently executing
    exists: bool                 # True if the file exists on disk
    size: int                    # file size in bytes (0 if missing)
    last_modified: Optional[float]  # epoch seconds, None if missing
    # Context (for labels / debugging):
    task_id: int
    report_id: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _log_dir_for_task(task: Task) -> Optional[Path]:
    working_dir = resolve_working_dir(task)
    if not working_dir:
        return None
    return Path(working_dir) / ".odin" / "logs"


def _stat(path: Path) -> tuple[bool, int, Optional[float]]:
    try:
        st = path.stat()
        return True, st.st_size, st.st_mtime
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return False, 0, None


def _reflection_trace_path(log_dir: Path, report_id: int) -> Path:
    jsonl = log_dir / f"reflect_{report_id}.trace.jsonl"
    if jsonl.exists():
        return jsonl
    out = log_dir / f"reflect_{report_id}.out"
    if out.exists():
        return out
    return jsonl


def _task_trace_path(task: Task, log_dir: Path, task_id: int) -> Path:
    """Return the best available trace file for a task.

    Resolution order:
    1. ``task.metadata["trace_file"]`` — odin records the absolute path at
       execution start; authoritative regardless of odin's launch cwd.
    2. ``{working_dir}/.odin/logs/`` — runs launched from the board root.
       Prefers .trace.jsonl (non-tmux harness execution), falls back to .out
       (tmux execution), which contains the same raw stream-json capture.
    3. ``{working_dir}/.odin/worktrees/{spec}/{task}/.odin/logs/`` — the
       celery executor runs odin with cwd = the task worktree, so a relative
       ``log_dir: .odin/logs`` resolves worktree-local (sandboxed and host
       runs alike). Needed for tasks dispatched before odin recorded the
       metadata path.
    """
    meta_path = (task.metadata or {}).get("trace_file")
    if meta_path:
        p = Path(meta_path)
        if p.exists():
            return p
    jsonl = log_dir / f"task_{task_id}.trace.jsonl"
    if jsonl.exists():
        return jsonl
    out = log_dir / f"task_{task_id}.out"
    if out.exists():
        return out
    spec_odin_id = getattr(task.spec, "odin_id", None) if task.spec_id else None
    working_dir = resolve_working_dir(task)
    if working_dir and spec_odin_id:
        wt = (
            Path(working_dir) / ".odin" / "worktrees" / spec_odin_id
            / str(task_id) / ".odin" / "logs" / f"task_{task_id}.trace.jsonl"
        )
        if wt.exists():
            return wt
    # Nothing exists yet — return the preferred path (may be created soon).
    return jsonl


def resolve_session(task: Task) -> Optional[SessionInfo]:
    """Return the current or most recent session for a task, or None.

    Caller is responsible for passing a Task fetched with any needed
    select_related(). This function hits the DB once (ReflectionReport lookup)
    and stats at most two files on disk.
    """
    log_dir = _log_dir_for_task(task)
    if log_dir is None:
        return None

    # 1. Live reflection takes priority.
    running_report = (
        ReflectionReport.objects
        .filter(task=task, status=ReflectionStatus.RUNNING)
        .order_by("-created_at")
        .first()
    )
    if running_report is not None:
        path = _reflection_trace_path(log_dir, running_report.id)
        exists, size, mtime = _stat(path)
        return SessionInfo(
            session_type=SESSION_TYPE_REFLECTION,
            jsonl_path=str(path),
            live=True,
            exists=exists,
            size=size,
            last_modified=mtime,
            task_id=task.id,
            report_id=running_report.id,
        )

    # 2. Live task execution.
    if task.status in _TASK_EXECUTING_STATUSES:
        path = _task_trace_path(task, log_dir, task.id)
        exists, size, mtime = _stat(path)
        return SessionInfo(
            session_type=SESSION_TYPE_TASK,
            jsonl_path=str(path),
            live=True,
            exists=exists,
            size=size,
            last_modified=mtime,
            task_id=task.id,
        )

    # 3. Idle — pick the most recently modified on-disk trace.
    task_path = _task_trace_path(task, log_dir, task.id)
    task_exists, task_size, task_mtime = _stat(task_path)

    # For the reflection fallback, use the latest ReflectionReport (any status)
    # — its trace file, if present, represents the last reflection run.
    latest_report = (
        ReflectionReport.objects
        .filter(task=task)
        .order_by("-created_at")
        .first()
    )
    refl_exists = False
    refl_size = 0
    refl_mtime: Optional[float] = None
    refl_path: Optional[Path] = None
    if latest_report is not None:
        refl_path = _reflection_trace_path(log_dir, latest_report.id)
        refl_exists, refl_size, refl_mtime = _stat(refl_path)

    # Choose whichever is newer.
    candidates = []
    if task_exists:
        candidates.append(("task", task_path, task_size, task_mtime))
    if refl_exists and refl_path is not None:
        candidates.append(("reflection", refl_path, refl_size, refl_mtime))

    if not candidates:
        return None

    candidates.sort(key=lambda c: c[3] or 0, reverse=True)
    kind, path, size, mtime = candidates[0]

    if kind == "task":
        return SessionInfo(
            session_type=SESSION_TYPE_TASK,
            jsonl_path=str(path),
            live=False,
            exists=True,
            size=size,
            last_modified=mtime,
            task_id=task.id,
        )
    else:
        return SessionInfo(
            session_type=SESSION_TYPE_REFLECTION,
            jsonl_path=str(path),
            live=False,
            exists=True,
            size=size,
            last_modified=mtime,
            task_id=task.id,
            report_id=latest_report.id if latest_report else None,
        )
