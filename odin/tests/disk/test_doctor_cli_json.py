"""End-to-end contract: ``odin doctor --json`` stdout is clean, parseable JSON.

Tags: [io] — writes log files in tmp_path; mocks the doctor probes so no real
services/CLIs are touched.

The machine-readable contract: a caller runs ``odin doctor --json`` and does
``json.load(sys.stdin)`` to get the report. This contract was BROKEN because
the CLI's "CLI initialized" INFO log was routed to stdout ahead of the JSON
blob, so naive parsing failed with ``Expecting ',' delimiter``. The cause was
fixed by routing console logs to stderr. These tests exercise the real
``OdinCLI.doctor`` method (including ``_get_config`` -> ``setup_logger`` ->
the log emission) to prove the contract holds end-to-end.
"""

from __future__ import annotations

import json
import logging

import pytest

from odin import cli as cli_module
from odin import doctor as doctor_mod
from odin.cli import OdinCLI
from odin.doctor import DoctorReport
from odin.models import OdinConfig


@pytest.fixture
def fresh_cli_logger():
    """The ``odin.cli`` logger caches handlers across tests in one process.

    Clear before and after so each test attaches a handler reflecting the
    current setup_logger wiring (stderr-bound), not a stale cached one.
    """
    log = logging.getLogger("odin.cli")
    log.handlers.clear()
    yield
    log.handlers.clear()


def _wire(tmp_path, monkeypatch):
    """Point OdinCLI at a throwaway config + log dir and stub the probes."""
    monkeypatch.setattr(
        cli_module,
        "load_config",
        lambda _path=None: OdinConfig(
            base_agent="codex",
            board_backend="local",
            log_dir=str(tmp_path / "logs"),
        ),
    )
    monkeypatch.setattr(doctor_mod, "run_doctor", lambda *a, **k: DoctorReport())
    return OdinCLI()


class TestDoctorJsonStdoutContract:
    def test_json_stdout_parses_directly_with_json_loads(
        self, tmp_path, monkeypatch, capfd, fresh_cli_logger,
    ):
        cli = _wire(tmp_path, monkeypatch)
        with pytest.raises(SystemExit):
            cli.doctor(json=True)
        out, err = capfd.readouterr()
        # The contract: stdout is valid JSON, parseable by a plain json.loads
        # (no log-line prefix, no leading noise).
        data = json.loads(out)
        assert {"agents", "checks", "features", "exit_code"} <= set(data)

    def test_cli_initialized_log_goes_to_stderr_not_stdout(
        self, tmp_path, monkeypatch, capfd, fresh_cli_logger,
    ):
        cli = _wire(tmp_path, monkeypatch)
        with pytest.raises(SystemExit):
            cli.doctor(json=True)
        out, err = capfd.readouterr()
        assert "CLI initialized" in err, "diagnostic log must land on stderr"
        assert "CLI initialized" not in out, (
            "the log line must not appear on stdout where it corrupts the JSON"
        )

    def test_human_output_still_writes_table_to_stdout(
        self, tmp_path, monkeypatch, capfd, fresh_cli_logger,
    ):
        """Regression guard: routing logs to stderr must not also move the
        human-readable doctor table off stdout — that is data, not diagnostics."""
        cli = _wire(tmp_path, monkeypatch)
        with pytest.raises(SystemExit):
            cli.doctor(json=False)
        out, err = capfd.readouterr()
        assert "Odin Doctor" in out, "human table is data -> stays on stdout"
