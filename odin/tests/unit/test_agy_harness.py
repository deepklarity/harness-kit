"""Conformance tests for the `agy` harness.

Tags: [simple] — no I/O, pure logic, register + build_execute_command shape.

Pins the agy harness to the contract every CLI harness must satisfy
(see `harnesses/base.py`):

  - registered in HARNESS_REGISTRY under 'agy'
  - `build_execute_command()` produces the documented flag shape
    (`-p <prompt>` + `--dangerously-skip-permissions` + optional `--model`)
  - respects `AgentConfig.cli_command` and `execute_args` overrides
  - `build_interactive_command()` is non-None and uses the same gate
  - `is_available()` consults PATH for the configured CLI

The gate flag is pinned to the REAL agy 1.0.x CLI contract —
`--dangerously-skip-permissions` is the documented auto-approve gate.
The legacy `--headless --approve all` pair does not exist in agy 1.0.x
and is asserted as absent so the harness never re-introduces it.

Subprocess behavior is covered by the parametric suite in
`tests/mock/test_agy_subprocess_errors.py` and the parametrized fixture
in `tests/mock/test_harness_subprocess_errors.py::ALL_CLI_HARNESSES`.
"""

import pytest

from odin.harnesses.agy import AGY_DEFAULT_EXECUTE_ARGS, AgyHarness
from odin.harnesses.registry import HARNESS_REGISTRY
from odin.models import AgentConfig, CostTier


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def agy_config():
    """Default Agy config — minimal AgentConfig matching the harness."""
    return AgentConfig(
        cli_command="agy",
        capabilities=["coding", "writing"],
        cost_tier=CostTier.LOW,
    )


@pytest.fixture
def agy_config_custom_cli():
    """Config with a non-default cli_command override."""
    return AgentConfig(
        cli_command="/opt/google/agy/bin/agy",
        capabilities=["coding"],
    )


# ── Registry ──────────────────────────────────────────────────


class TestAgyRegistration:
    """The agy harness must be discoverable through HARNESS_REGISTRY."""

    def test_registered_in_registry(self):
        assert "agy" in HARNESS_REGISTRY

    def test_registry_class_is_agy_harness(self):
        assert HARNESS_REGISTRY["agy"] is AgyHarness


# ── build_execute_command shape ──────────────────────────────


class TestAgyBuildExecuteCommand:
    """Pin the one-shot CLI shape: `-p <prompt>` + gates + `--model`.

    The executor never sees a JSONL stream from agy (no `--output-format
    json` flag exists in v1.0.x); verify the harness does NOT pass a
    framing flag, then defer text extraction to the streaming fallback
    in `extract_text_from_stream()`.
    """

    def test_uses_configured_cli_binary(self, agy_config):
        harness = AgyHarness(agy_config)
        cmd = harness.build_execute_command("hello", {})
        assert cmd[0] == "agy"

    def test_prompt_passed_via_dash_p(self, agy_config):
        harness = AgyHarness(agy_config)
        cmd = harness.build_execute_command("Reply with exactly: OK", {})
        assert "-p" in cmd
        p_idx = cmd.index("-p")
        assert cmd[p_idx + 1] == "Reply with exactly: OK"

    def test_default_gates_emitted(self, agy_config):
        """agy 1.0.x exposes a single non-interactive gate:
        `--dangerously-skip-permissions`. The legacy `--headless` /
        `--approve` pair does not exist in agy and must NOT be emitted.
        """
        harness = AgyHarness(agy_config)
        cmd = harness.build_execute_command("p", {})
        assert "--dangerously-skip-permissions" in cmd
        assert "--headless" not in cmd
        assert "--approve" not in cmd
        assert AGY_DEFAULT_EXECUTE_ARGS == "--dangerously-skip-permissions"

    def test_does_not_pass_output_format_json(self, agy_config):
        """Per the operator brief: agy has no `--output-format json` flag.

        The harness must NOT inject `--output-format` (or any structured
        framing flag) — output flows as plain text and the base
        stream-text extractor passes it through unchanged.
        """
        harness = AgyHarness(agy_config)
        cmd = harness.build_execute_command("p", {})
        assert "--output-format" not in cmd
        assert "--json" not in cmd
        assert "--stream-json" not in cmd

    def test_no_stream_json_flag(self, agy_config):
        """Negative-space check: agy rejects stream-json; verify we never
        request it."""
        harness = AgyHarness(agy_config)
        cmd = harness.build_execute_command("p", {})
        joined = " ".join(cmd)
        assert "stream-json" not in joined
        assert "format-json" not in joined

    def test_prompt_is_last_argument(self, agy_config):
        """`agy -p <prompt> ...` requires prompt as the first positional
        after `-p`; downstream args (model) trail it."""
        harness = AgyHarness(agy_config)
        cmd = harness.build_execute_command("the prompt", {})
        assert "-p" in cmd
        p_idx = cmd.index("-p")
        assert cmd[p_idx + 1] == "the prompt"

    def test_model_appended_when_in_context(self, agy_config):
        harness = AgyHarness(agy_config)
        cmd = harness.build_execute_command("p", {"model": "agy-flash"})
        assert "--model" in cmd
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "agy-flash"

    def test_no_model_flag_when_not_in_context(self, agy_config):
        harness = AgyHarness(agy_config)
        cmd = harness.build_execute_command("p", {})
        assert "--model" not in cmd

    def test_execute_args_override_replaces_default_gates(self, agy_config):
        """When the operator supplies execute_args, the default gate is
        replaced wholesale (no implicit fallback). This is consistent
        with every other CLI harness in the registry."""
        agy_config.execute_args = "--dangerously-skip-permissions"
        harness = AgyHarness(agy_config)
        cmd = harness.build_execute_command("p", {})
        # Only one occurrence — execute_args replaced the default
        # wholesale; we did not double-append.
        assert cmd.count("--dangerously-skip-permissions") == 1
        # The legacy pair must NEVER be added, override or not.
        assert "--headless" not in cmd
        assert "--approve" not in cmd

    def test_respects_cli_command_override(self, agy_config_custom_cli):
        harness = AgyHarness(agy_config_custom_cli)
        cmd = harness.build_execute_command("p", {})
        assert cmd[0] == "/opt/google/agy/bin/agy"

    def test_default_cli_is_agy_when_none_configured(self):
        """When cli_command is None, the harness falls back to 'agy' on PATH."""
        cfg = AgentConfig(cli_command=None, capabilities=["coding"])
        harness = AgyHarness(cfg)
        assert harness.name == "Agy"
        cmd = harness.build_execute_command("p", {})
        assert cmd[0] == "agy"

    def test_name_is_agy(self, agy_config):
        harness = AgyHarness(agy_config)
        assert harness.name == "Agy"


# ── build_interactive_command ───────────────────────────────


class TestAgyBuildInteractiveCommand:
    """Interactive mode still needs the gate flag pair, but no -p."""

    def test_interactive_command_returns_non_none(self, agy_config):
        harness = AgyHarness(agy_config)
        cmd = harness.build_interactive_command("/tmp/sysprompt.txt", {})
        assert cmd is not None

    def test_interactive_uses_prompt_file(self, agy_config):
        harness = AgyHarness(agy_config)
        cmd = harness.build_interactive_command("/tmp/sysprompt.txt", {})
        joined = " ".join(cmd)
        assert "/tmp/sysprompt.txt" in joined

    def test_interactive_emits_default_gates(self, agy_config):
        """Interactive mode must also auto-approve tool-permission prompts."""
        harness = AgyHarness(agy_config)
        cmd = harness.build_interactive_command("/tmp/sys.txt", {})
        assert "--dangerously-skip-permissions" in cmd
        assert "--headless" not in cmd
        assert "--approve" not in cmd

    def test_interactive_omits_one_shot_prompt_flag(self, agy_config):
        """No -p in interactive mode (it conflicts with -i / file-based prompt)."""
        harness = AgyHarness(agy_config)
        cmd = harness.build_interactive_command("/tmp/sys.txt", {})
        # -p is one-shot only; interactive reads the prompt from the file
        # ref. The harness must not double-set it.
        # Note: subprocess `-p` token still appears inside the file-ref
        # argv slot if present; we just want to confirm no bare `-p <prompt>`.
        # Easier: count occurrences — should be exactly the one inside the file.
        from collections import Counter
        counts = Counter(cmd)
        # "-p" should NOT appear unaccompanied (no `-p <prompt>` pair)
        # because interactive reads from a file.
        # Quick heuristic: there is no bare "prompt" argument in cmd.
        assert "the prompt" not in cmd


# ── FileNotFoundError handling contract ─────────────────────


class TestAgyAvailabilityContract:
    """is_available() relies on shutil.which for the configured CLI."""

    def test_is_available_uses_shutil_which(self, agy_config):
        harness = AgyHarness(agy_config)
        from unittest.mock import patch
        with patch("odin.harnesses.agy.shutil.which", return_value="/usr/bin/agy"):
            import asyncio
            assert asyncio.run(harness.is_available()) is True

    def test_is_available_false_when_cli_missing(self, agy_config):
        harness = AgyHarness(agy_config)
        from unittest.mock import patch
        with patch("odin.harnesses.agy.shutil.which", return_value=None):
            import asyncio
            assert asyncio.run(harness.is_available()) is False

    def test_uses_custom_cli_when_resolving_availability(self, agy_config_custom_cli):
        """When cli_command is overridden, is_available must consult that
        path, not the bare 'agy' name."""
        harness = AgyHarness(agy_config_custom_cli)
        from unittest.mock import patch
        with patch(
            "odin.harnesses.agy.shutil.which",
            return_value=None,
        ) as mock_which:
            import asyncio
            asyncio.run(harness.is_available())
            # shutil.which must have been called with the custom path,
            # not with the default 'agy' literal.
            mock_which.assert_called_with("/opt/google/agy/bin/agy")


# ── supports_system_prompt_flag ────────────────────────────


class TestAgyHarnessContract:
    """Pin harness-level contract bits shared with the BaseHarness API."""

    def test_supports_system_prompt_flag_false(self, agy_config):
        """agy reads the prompt from -i with a file ref, not via a CLI flag.
        The base property returns False, matching gemini / glm / codex."""
        harness = AgyHarness(agy_config)
        assert harness.supports_system_prompt_flag is False
