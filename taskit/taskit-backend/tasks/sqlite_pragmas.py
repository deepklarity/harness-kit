"""SQLite connection pragmas: WAL journal mode + busy_timeout.

Applied on every new connection via Django's ``connection_created``
signal, so the app server, Celery workers, management commands, and the
test runner all share one configuration from a single ``apps.ready()``
hook — there is no second settings path to forget.

Why WAL: the default ``delete`` journal takes an exclusive lock for the
duration of every write. The backend has many concurrent writers (dag
poll, merge scan, reflection scan, reconciler, watchers, sandbox
comments), so exclusive-write locking made ``database is locked`` a
failure class rather than a fluke. WAL lets readers and one writer
coexist, and ``busy_timeout`` tells a blocked writer to wait rather than
fail the instant contention appears.

If locks persist past the busy_timeout under sustained heavy load,
migrate to PostgreSQL (noted in the Trust bucket) — SQLite remains the
dev/default store.
"""

import logging

from django.db.backends.signals import connection_created

logger = logging.getLogger("taskit.sqlite_pragmas")

_DEFAULT_BUSY_TIMEOUT_MS = 5000


def _resolve_busy_timeout():
    # Imported lazily so test ``override_settings`` and late settings
    # mutation are honoured at connection time, not at import time.
    from django.conf import settings

    return int(
        getattr(settings, "SQLITE_BUSY_TIMEOUT_MS", _DEFAULT_BUSY_TIMEOUT_MS)
        or _DEFAULT_BUSY_TIMEOUT_MS
    )


def apply_sqlite_pragmas(sender, connection, **kwargs):
    """Set WAL journal mode + busy_timeout on a fresh SQLite connection.

    No-op for non-sqlite vendors (e.g. the PostgreSQL prod store) so the
    same settings module is safe in both deployments.
    """
    if connection.vendor != "sqlite":
        return
    busy_timeout_ms = _resolve_busy_timeout()
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=%d" % busy_timeout_ms)


def connect():
    """Wire the pragma handler to Django's ``connection_created`` signal.

    Called from ``TasksConfig.ready`` so every process that boots the
    Django app registry applies the pragmas.
    """
    connection_created.connect(apply_sqlite_pragmas)
    logger.debug("SQLite WAL/busy_timeout pragma handler connected")
