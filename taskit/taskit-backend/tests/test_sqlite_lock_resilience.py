"""SQLite lock resilience (task #253).

Two layers, both mandated by the acceptance:

  1. Connection pragmas — WAL journal mode + busy_timeout applied on every
     new SQLite connection (the many-readers-few-writers tuning). Without
     WAL the default ``delete`` journal takes an exclusive lock on every
     write, so the concurrent pollers/watchers collide and an execution dies
     on ``database is locked`` (the failure that killed task 251).
  2. Retry-on-locked — the heartbeat hot write (touched every few seconds
     by the subprocess-monitoring loop in ``_run_subprocess_with_cancellation``)
     retries a transient ``OperationalError: database is locked`` instead of
     letting the broad ``except`` turn it into a ``spawn_exception`` kill.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from unittest.mock import MagicMock, patch

from django.db import OperationalError, connection
from django.db.backends.signals import connection_created
from django.test import override_settings

from tests.base import APITestCase
from tasks import db as db_module
from tasks import task_runs
from tasks.models import TaskStatus
from tasks.sqlite_pragmas import apply_sqlite_pragmas


class SqlitePragmaWiringTests(APITestCase):
    """The handler is wired to Django's connection_created signal."""

    def test_handler_dispatched_via_connection_created(self):
        # Dispatch the real signal with a fake sqlite connection. If
        # TasksConfig.ready() wired apply_sqlite_pragmas, the handler runs
        # and issues the pragmas — proving the end-to-end wiring (this is
        # how runserver, celery workers, and the test runner trigger it).
        self.assertTrue(connection_created.has_listeners())
        fake_cursor = MagicMock()
        fake_conn = MagicMock()
        fake_conn.vendor = "sqlite"
        fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

        connection_created.send(sender=None, connection=fake_conn)

        executed = [c.args[0] for c in fake_cursor.execute.call_args_list]
        self.assertTrue(any("journal_mode" in s and "WAL" in s.upper() for s in executed))
        self.assertTrue(any("busy_timeout" in s for s in executed))


class SqlitePragmaFileIntegrationTests(APITestCase):
    """End-to-end proof the handler turns journal_mode into WAL.

    Django's test runner uses an in-memory database
    (``file:memorydb_default?mode=memory&cache=shared``), and SQLite always
    reports ``memory`` for in-memory DBs regardless of the WAL pragma. Prod
    uses a file DB (``db.sqlite3``) where WAL takes hold, so this test
    drives the real handler against a real file-based sqlite connection —
    the exact acceptance criterion ("PRAGMA journal_mode returns wal").
    """

    def _file_connection(self, path):
        import sqlite3

        class _Cursor:
            def __init__(self, raw):
                self._raw = raw

            def execute(self, sql, params=None):
                return self._raw.execute(sql, params or [])

            def fetchone(self):
                return self._raw.fetchone()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                pass

        class _Conn:
            vendor = "sqlite"

            def __init__(self, raw):
                self._raw = raw

            def cursor(self):
                return _Cursor(self._raw.cursor())

        return _Conn(sqlite3.connect(path))

    def test_journal_mode_is_wal_on_file_based_db(self):
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        try:
            conn = self._file_connection(path)
            apply_sqlite_pragmas(None, conn)
            with conn.cursor() as cursor:
                cursor.execute("PRAGMA journal_mode")
                mode = cursor.fetchone()[0]
            self.assertEqual(mode.lower(), "wal")
        finally:
            os.unlink(path)

    def test_busy_timeout_applied_on_file_based_db(self):
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        try:
            conn = self._file_connection(path)
            with override_settings(SQLITE_BUSY_TIMEOUT_MS=12000):
                apply_sqlite_pragmas(None, conn)
            with conn.cursor() as cursor:
                cursor.execute("PRAGMA busy_timeout")
                value = cursor.fetchone()[0]
            self.assertEqual(value, 12000)
        finally:
            os.unlink(path)



class ApplySqlitePragmasUnitTests(APITestCase):
    """The handler logic, exercised directly with a fake connection."""

    def test_sets_wal_and_busy_timeout_for_sqlite(self):
        fake_cursor = MagicMock()
        fake_conn = MagicMock()
        fake_conn.vendor = "sqlite"
        fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

        with override_settings(SQLITE_BUSY_TIMEOUT_MS=9000):
            apply_sqlite_pragmas(None, fake_conn)

        executed = [call.args[0] for call in fake_cursor.execute.call_args_list]
        self.assertTrue(
            any("journal_mode" in s and "WAL" in s.upper() for s in executed),
            f"WAL pragma not set: {executed}",
        )
        self.assertTrue(
            any("busy_timeout" in s and "9000" in s for s in executed),
            f"busy_timeout=9000 not set: {executed}",
        )

    def test_skips_non_sqlite_vendor(self):
        # The same settings module powers the PostgreSQL prod store; the
        # handler must be a no-op there so WAL pragmas never reach Postgres.
        fake_conn = MagicMock()
        fake_conn.vendor = "postgresql"
        apply_sqlite_pragmas(None, fake_conn)
        fake_conn.cursor.assert_not_called()


class RetryOnLockedUnitTests(APITestCase):
    """The retry decorator — the bounded backstop for the rare case where
    busy_timeout itself is exceeded under heavy concurrent write load."""

    def test_retries_locked_then_succeeds(self):
        calls = []

        @db_module.retry_on_locked(max_retries=3, base_delay=0)
        def write():
            calls.append(1)
            if len(calls) < 3:
                raise OperationalError("database is locked")
            return "ok"

        self.assertEqual(write(), "ok")
        self.assertEqual(len(calls), 3)

    def test_reraises_after_exhausting_retries(self):
        calls = []

        @db_module.retry_on_locked(max_retries=2, base_delay=0)
        def write():
            calls.append(1)
            raise OperationalError("database is locked")

        with self.assertRaises(OperationalError):
            write()
        # initial attempt + 2 retries == 3 calls
        self.assertEqual(len(calls), 3)

    def test_does_not_retry_non_locked_error(self):
        # A non-lock OperationalError (e.g. schema problem) must surface
        # immediately — retrying masks a real bug.
        calls = []

        @db_module.retry_on_locked(max_retries=3, base_delay=0)
        def write():
            calls.append(1)
            raise OperationalError("no such table: tasks_taskrun")

        with self.assertRaises(OperationalError):
            write()
        self.assertEqual(len(calls), 1)

    def test_successful_call_is_not_retried(self):
        calls = []

        @db_module.retry_on_locked(max_retries=3, base_delay=0)
        def write():
            calls.append(1)
            return 42

        self.assertEqual(write(), 42)
        self.assertEqual(len(calls), 1)


class HeartbeatRetryOnLockedTests(APITestCase):
    """The known hot write (heartbeat_run) retries a mocked lock and
    succeeds — the acceptance criterion for layer 2."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board, status=TaskStatus.EXECUTING)

    @patch("tasks.db.time.sleep")
    def test_heartbeat_retries_locked_write_then_succeeds(self, _sleep):
        # A real RUNNING row so the non-mocked path is exercised up to the
        # write; only the final .update() is mocked to fail-then-succeed.
        task_runs.start_run(self.task, "tok")

        qs = MagicMock()
        qs.update.side_effect = [
            OperationalError("database is locked"),
            1,  # success on retry
        ]
        with patch("tasks.task_runs.TaskRun") as mock_model:
            mock_model.objects.filter.return_value = qs
            task_runs.heartbeat_run("tok")

        self.assertEqual(qs.update.call_count, 2)

    @patch("tasks.db.time.sleep")
    def test_heartbeat_propagates_after_retries_exhausted(self, _sleep):
        task_runs.start_run(self.task, "tok")

        qs = MagicMock()
        qs.update.side_effect = OperationalError("database is locked")
        with patch("tasks.task_runs.TaskRun") as mock_model:
            mock_model.objects.filter.return_value = qs
            with self.assertRaises(OperationalError):
                task_runs.heartbeat_run("tok")

        # default decorator bounds retries; eventually the lock wins and
        # the error surfaces (so the caller's broad except still classifies
        # it via the failure_tagger rather than swallowing it silently).
        self.assertGreater(qs.update.call_count, 1)
