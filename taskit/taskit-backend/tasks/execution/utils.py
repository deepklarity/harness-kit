"""Shared utilities for execution strategies."""

import logging

from django.conf import settings

logger = logging.getLogger("taskit.execution")


def resolve_working_dir(task):
    """Resolve the working directory for a task.

    Priority:
        1. task.metadata["working_dir"] — per-task override
        2. task.board.working_dir — board-level project directory (preferred)
        3. task.spec.metadata["working_dir"] — deprecated, logs warning
        4. settings.ODIN_WORKING_DIR — deprecated, logs warning
    """
    # 1. Task-level override (highest priority)
    working_dir = (task.metadata or {}).get("working_dir")
    if working_dir:
        return working_dir

    # 2. Board-level working dir (the new canonical source)
    board = getattr(task, "board", None)
    if board and board.working_dir:
        return board.working_dir

    # 3. Spec metadata (deprecated fallback)
    if task.spec_id:
        spec_working_dir = (task.spec.metadata or {}).get("working_dir")
        if spec_working_dir:
            logger.warning(
                "Task %s: using deprecated spec.metadata.working_dir fallback. "
                "Set board.working_dir instead.",
                task.id,
            )
            return spec_working_dir

    # 4. Settings fallback (deprecated)
    env_working_dir = getattr(settings, "ODIN_WORKING_DIR", None)
    if env_working_dir:
        logger.warning(
            "Task %s: using deprecated ODIN_WORKING_DIR setting fallback. "
            "Set board.working_dir instead.",
            task.id,
        )
        return env_working_dir

    return None
