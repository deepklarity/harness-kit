"""Kanban ordering helpers shared across manual and system status transitions."""

from __future__ import annotations

from typing import Iterable, List

from django.db import transaction

from .models import Task, TaskStatus


KANBAN_COLUMNS: List[str] = [
    TaskStatus.BACKLOG,
    TaskStatus.TODO,
    TaskStatus.IN_PROGRESS,
    TaskStatus.REVIEW,
    TaskStatus.TESTING,
    TaskStatus.DONE,
    TaskStatus.FAILED,
]


def get_column_for_status(status: str) -> str:
    """Map raw task status to visual Kanban column key."""
    if status == TaskStatus.EXECUTING:
        return TaskStatus.IN_PROGRESS
    return status


def get_statuses_for_column(column_status: str) -> List[str]:
    if column_status == TaskStatus.IN_PROGRESS:
        return [TaskStatus.IN_PROGRESS, TaskStatus.EXECUTING]
    return [column_status]


def _column_tasks(board_id: int, column_status: str, exclude_task_id: int | None = None) -> List[Task]:
    qs = (
        Task.objects
        .filter(board_id=board_id, status__in=get_statuses_for_column(column_status))
        .order_by("kanban_position", "id")
    )
    if exclude_task_id is not None:
        qs = qs.exclude(id=exclude_task_id)
    return list(qs)


def _persist_dense_positions(tasks: Iterable[Task]) -> None:
    to_update = []
    for idx, task in enumerate(tasks):
        if task.kanban_position != idx:
            task.kanban_position = idx
            to_update.append(task)

    # ⚡ Bolt optimization: Use bulk_update to eliminate N+1 query problem.
    # Replaces O(N) individual updates with a single batched query,
    # significantly reducing database load when reordering large columns.
    if to_update:
        Task.objects.bulk_update(to_update, ["kanban_position"], batch_size=500)


def move_task(task: Task, target_status: str, target_index: int | None = None) -> int:
    """Reposition a task in Kanban order.

    - Manual move/reorder: pass explicit ``target_index``.
    - System move: pass ``target_index=None`` and task is inserted at top on
      cross-column transitions.
    """
    source_column = get_column_for_status(task.status)
    target_column = get_column_for_status(target_status)

    with transaction.atomic():
        if source_column != target_column:
            source_tasks = _column_tasks(
                board_id=task.board_id,
                column_status=source_column,
                exclude_task_id=task.id,
            )
            _persist_dense_positions(source_tasks)

        if target_index is None and source_column == target_column:
            return int(task.kanban_position or 0)

        destination_tasks = _column_tasks(
            board_id=task.board_id,
            column_status=target_column,
            exclude_task_id=task.id,
        )

        if target_index is None:
            target_index = 0

        target_index = max(0, min(int(target_index), len(destination_tasks)))
        destination_tasks.insert(target_index, task)
        _persist_dense_positions(destination_tasks)
        return target_index
