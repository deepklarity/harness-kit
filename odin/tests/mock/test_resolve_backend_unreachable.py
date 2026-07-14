"""Tests for backend-unreachable handling in task ID resolution (task #339).

A transient backend outage during resolve_task_id must not be confused
with a genuinely wrong ID. The wrong-ID path returns None (helpful
"no match" message); the unreachable path raises BackendUnreachable so
the CLI can retry and, on persistent failure, exit with a message the
TaskIt executor classifies as infra (not crash).
"""

from unittest.mock import MagicMock, patch

import pytest

from odin.cli import OdinCLI
from odin.taskit import TaskManager
from odin.taskit.manager import BackendUnreachable


# ── resolve_task_id: distinguish unreachable from wrong-id ───────────


def test_resolve_raises_on_backend_exception():
    """When load_all_tasks raises, resolve_task_id raises BackendUnreachable."""
    backend = MagicMock()
    backend.load_all_tasks.side_effect = ConnectionError("connection refused")
    mgr = TaskManager("/tmp/odin-test-resolve", backend=backend)
    with pytest.raises(BackendUnreachable, match="connection refused"):
        mgr.resolve_task_id("abc123")


def test_resolve_raises_on_empty_from_configured_backend():
    """A configured backend returning [] is suspicious — raise BackendUnreachable."""
    backend = MagicMock()
    backend.load_all_tasks.return_value = []
    mgr = TaskManager("/tmp/odin-test-resolve", backend=backend)
    with pytest.raises(BackendUnreachable):
        mgr.resolve_task_id("abc123")


def test_resolve_returns_none_for_wrong_id_when_backend_reachable():
    """Backend reachable + tasks returned + no match → None (wrong id)."""
    task = MagicMock()
    task.id = "deadbeef1234"
    backend = MagicMock()
    backend.load_all_tasks.return_value = [task]
    mgr = TaskManager("/tmp/odin-test-resolve", backend=backend)
    assert mgr.resolve_task_id("zzzzzzz") is None


def test_resolve_returns_none_for_empty_local_disk(task_mgr):
    """No backend (local disk) + empty → None (genuinely empty board)."""
    assert task_mgr.resolve_task_id("zzzzzzz") is None


def test_resolve_returns_full_id_when_backend_reachable():
    """Backend reachable + one match → full ID."""
    task = MagicMock()
    task.id = "deadbeef1234"
    backend = MagicMock()
    backend.load_all_tasks.return_value = [task]
    mgr = TaskManager("/tmp/odin-test-resolve", backend=backend)
    assert mgr.resolve_task_id("dead") == "deadbeef1234"


# ── _resolve_id: retry on unreachable, no retry on wrong-id ─────────


def _cli_with_manager(resolve_side_effect):
    """Build an OdinCLI whose _get_task_manager returns a mock manager."""
    cli = OdinCLI()
    mgr = MagicMock()
    mgr.resolve_task_id.side_effect = resolve_side_effect
    cli._get_task_manager = lambda: mgr
    return cli, mgr


def _printed_text(mock_console):
    parts = []
    for call_obj in mock_console.print.call_args_list:
        args, _ = call_obj
        parts.extend(str(a) for a in args)
    return " ".join(parts)


@patch("odin.cli.time.sleep")
def test_resolve_id_retries_then_succeeds_on_unreachable(mock_sleep):
    """A transient BackendUnreachable is retried and succeeds on recovery."""
    full_id = "deadbeef1234"
    cli, mgr = _cli_with_manager([
        BackendUnreachable("connection refused"),
        full_id,
    ])
    assert cli._resolve_id("dead") == full_id
    assert mgr.resolve_task_id.call_count == 2
    mock_sleep.assert_called()


@patch("odin.cli.time.sleep")
def test_resolve_id_gives_up_with_infra_message_after_retries(mock_sleep):
    """Persistent BackendUnreachable → SystemExit with a message naming the
    real problem so the executor classifies it as infra, not crash."""
    cli, mgr = _cli_with_manager(BackendUnreachable("connection refused"))
    with patch("odin.cli.console") as mock_console:
        with pytest.raises(SystemExit) as exc_info:
            cli._resolve_id("dead")
    assert exc_info.value.code == 1
    assert mgr.resolve_task_id.call_count == 3
    text = _printed_text(mock_console).lower()
    assert "backend unreachable" in text or "could not reach task backend" in text


@patch("odin.cli.time.sleep")
def test_resolve_id_wrong_id_no_retry(mock_sleep):
    """A genuinely wrong ID (None) is not retried — old helpful message."""
    cli = OdinCLI()
    mgr = MagicMock()
    mgr.resolve_task_id.return_value = None
    cli._get_task_manager = lambda: mgr
    with patch("odin.cli.console") as mock_console:
        with pytest.raises(SystemExit) as exc_info:
            cli._resolve_id("zzzzzzz")
    assert exc_info.value.code == 1
    assert mgr.resolve_task_id.call_count == 1
    mock_sleep.assert_not_called()
    text = _printed_text(mock_console)
    assert "Could not resolve task ID" in text
    assert "No match or ambiguous prefix" in text
