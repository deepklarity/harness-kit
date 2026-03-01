"""Execution strategy registry."""

import importlib
from typing import Optional

from django.conf import settings

from .base import ExecutionStrategy
from ..utils.logger import logger


_STRATEGIES = {
    "local": "tasks.execution.local.LocalOdinStrategy",
    "celery_dag": "tasks.execution.celery_dag.CeleryDAGStrategy",
}


def get_strategy() -> Optional[ExecutionStrategy]:
    """Return the configured execution strategy, or None if disabled."""
    strategy_name = getattr(settings, "ODIN_EXECUTION_STRATEGY", None)
    if not strategy_name:
        logger.debug("No ODIN_EXECUTION_STRATEGY configured — execution trigger disabled")
        return None

    dotted = _STRATEGIES.get(strategy_name)
    if not dotted:
        logger.warning("Unknown execution strategy: %s (available: %s)", strategy_name, list(_STRATEGIES.keys()))
        return None

    module_path, cls_name = dotted.rsplit(".", 1)
    module = importlib.import_module(module_path)
    cls = getattr(module, cls_name)
    logger.info("Loaded execution strategy: %s", strategy_name)
    return cls()
