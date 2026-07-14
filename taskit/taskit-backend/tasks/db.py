"""Database-resilience helpers.

``retry_on_locked`` is the bounded backstop for the rare case where the
SQLite ``busy_timeout`` is itself exceeded under heavy concurrent write
load. WAL + busy_timeout (see ``tasks.sqlite_pragmas``) absorbs normal
contention; this catches the tail so a periodic hot write does not kill
the surrounding execution.

Only ``database is locked`` errors are retried — every other
``OperationalError`` (schema problems, disk-full, etc.) surfaces
immediately, because retrying those would mask a real bug.
"""

import functools
import logging
import time

from django.db import OperationalError

logger = logging.getLogger("taskit.db")

# SQLite reports a write-contention failure with one of these phrases.
# ``OperationalError`` carries no error code in the sqlite3 binding, so the
# message is the only discriminator.
_LOCK_MARKERS = ("is locked", "database locked")


def is_locked_error(exc):
    """True if ``exc`` is a SQLite write-contention failure.

    The single source of truth for lock detection — shared by
    ``retry_on_locked`` (retry the write) and the best-effort decoupling in
    ``execution_result`` (downgrade to a warning after retries are exhausted).
    """
    msg = str(exc).lower()
    return any(marker in msg for marker in _LOCK_MARKERS)


def retry_on_locked(max_retries=3, base_delay=0.05):
    """Retry a DB write that fails with ``database is locked``.

    Retries with exponential backoff (base_delay * 2**attempt), bounded by
    ``max_retries`` extra attempts. Non-lock errors propagate unchanged.

    Used by the TaskRun heartbeat (touched every few seconds by the
    subprocess-monitoring loop) so a transient lock at the exact second a
    writer collides cannot turn into a ``spawn_exception`` kill of the run.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except OperationalError as exc:
                    if not is_locked_error(exc) or attempt >= max_retries:
                        raise
                    attempt += 1
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "SQLite locked on %s; retry %d/%d after %.3fs",
                        func.__name__, attempt, max_retries, delay,
                    )
                    time.sleep(delay)

        return wrapper

    return decorator
