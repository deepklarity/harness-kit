"""Tests that odin's console logging routes to stderr, not stdout.

Tags: [io] — creates log files in tmp_path.

Why this matters: ``odin doctor --json`` (and any machine-readable CLI output)
must emit ONLY the structured payload on stdout so a caller's
``json.load(sys.stdin)`` works. Routing console logs to stdout pollutes that
contract — a leading ``[...] INFO CLI initialized`` line makes the output
unparseable. The Unix convention is: diagnostics/logs -> stderr, data ->
stdout. These tests pin that the console handler ``setup_logger`` wires
honors it.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from odin.logging.logger_utils import setup_logger


def _fresh_logger(name: str, tmp_path) -> logging.Logger:
    """Return a logger whose handlers were just attached by setup_logger.

    Clears cached handlers first so each test observes the stream setup_logger
    currently wires (not a handler attached during a prior test). Disables
    propagation so records do not reach a parent logger that might still hold
    a stdout handler.
    """
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.propagate = False
    return setup_logger(name, log_dir=str(tmp_path / "logs"))


def _console_handlers(logger: logging.Logger) -> list[logging.StreamHandler]:
    return [
        h for h in logger.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, RotatingFileHandler)
    ]


class TestConsoleHandlerStream:
    def test_console_handler_uses_stderr(self, tmp_path):
        logger = _fresh_logger("odin.test_stderr_unit", tmp_path)
        console = _console_handlers(logger)
        assert console, "setup_logger must attach a console StreamHandler"
        assert console[0].stream is sys.stderr, (
            "console logs must go to stderr so machine output (e.g. "
            "`odin doctor --json`) stays a clean data stream on stdout"
        )

    def test_no_console_handler_points_at_stdout(self, tmp_path):
        logger = _fresh_logger("odin.test_no_stdout", tmp_path)
        stdout_handlers = [
            h for h in _console_handlers(logger) if h.stream is sys.stdout
        ]
        assert stdout_handlers == [], (
            "no console handler may write to stdout — that breaks the "
            "--json machine-output contract (json.load fails on the log prefix)"
        )

    def test_log_record_lands_on_stderr_not_stdout(self, tmp_path, capfd):
        logger = _fresh_logger("odin.test_runtime_stderr", tmp_path)
        logger.info("DIAGNOSTIC MARKER 7781")
        out, err = capfd.readouterr()
        assert "DIAGNOSTIC MARKER 7781" in err, (
            "console log records must appear on stderr"
        )
        assert "DIAGNOSTIC MARKER 7781" not in out, (
            "console log records must NOT pollute stdout"
        )
