"""Conformance tests for the `claude` harness.

Tags: [simple] — no I/O, pure logic, register + build_execute_command shape.

Pins the claude CLI harness to the contract every CLI harness must satisfy
(see `harnesses/base.py`):

  - registered in HARNESS_REGISTRY under 'claude'
  - `build_execute_command()` produces the documented flag shape
    (`-p <prompt>` + `--output-format stream-json` + `--verbose` + `--model`)
  - `--setting-sources` is OPT-IN via `context["setting_sources"]`; the
    default (regular task execution) does NOT emit it, so project-level
    `.claude/settings.local.json` safety hooks (secrets / lock-file edit
    blocks) keep loading. Only the reflection reviewer opts in via
    `setting_sources="user"` to skip project/local discovery and avoid the
    "Ignoring N permissions.allow entries ... workspace has not been
    trusted" warning that polluted task 159's reviewer output (4
    reflections hard-ERRORed on that single noise line).
  - `build_execute_command()` and `build_interactive_command()` stay in
    parity for the shared gates (model, mcp_config, mcp_allowed_tools,
    setting_sources).
"""

import pytest

from odin.harnesses.claude import ClaudeHarness
from odin.harnesses.registry import HARNESS_REGISTRY
from odin.models import AgentConfig


@pytest.fixture
def claude_config():
    return AgentConfig(
        cli_command="claude",
        capabilities=["coding", "writing"],
    )


class TestClaudeRegistration:
    def test_registered_in_registry(self):
        assert "claude" in HARNESS_REGISTRY

    def test_registry_class_is_claude_harness(self):
        assert HARNESS_REGISTRY["claude"] is ClaudeHarness


class TestClaudeBuildExecuteCommand:
    """Pin the one-shot CLI shape and the opt-in setting_sources contract."""

    def test_uses_configured_cli_binary(self, claude_config):
        harness = ClaudeHarness(claude_config)
        cmd = harness.build_execute_command("hello", {})
        assert cmd[0] == "claude"

    def test_prompt_passed_via_dash_p(self, claude_config):
        harness = ClaudeHarness(claude_config)
        cmd = harness.build_execute_command("Review the code", {})
        assert "-p" in cmd
        assert cmd[cmd.index("-p") + 1] == "Review the code"

    def test_stream_json_output_format(self, claude_config):
        harness = ClaudeHarness(claude_config)
        cmd = harness.build_execute_command("p", {})
        assert "--output-format" in cmd
        assert "stream-json" in cmd

    def test_no_setting_sources_by_default(self, claude_config):
        """Regular task execution must NOT auto-emit --setting-sources.

        The default behavior is to let project/local `.claude/settings.*`
        load normally — including `.claude/settings.local.json` safety
        hooks (secrets / lock-file edit blocks). Emitting --setting-sources
        unconditionally would silently disable those hooks for every task
        on the board (task 165 review feedback — the previous implementation
        scoped the flag into build_execute_command unconditionally, which
        was NEEDS_WORK because it disabled safety hooks for regular task
        execution, not just reviewer runs).
        """
        harness = ClaudeHarness(claude_config)
        cmd = harness.build_execute_command("p", {})
        assert "--setting-sources" not in cmd, (
            "Regular task execution must NOT auto-emit --setting-sources — "
            "doing so silently disables project-level Claude Code safety "
            "hooks (secrets / lock-file edit blocks) for every task. The "
            "reviewer is the only consumer that should opt in."
        )

    def test_setting_sources_from_context_emitted(self, claude_config):
        """Reviewer sets context['setting_sources']='user' → flag emitted.

        The reflection reviewer needs to skip project/local settings
        discovery because the worktree carries a stale
        `.claude/settings.local.json` whose `permissions.allow` entries
        trigger the workspace-trust noise warning (task 159: 4 reflections
        hard-ERRORed on that single line).
        """
        harness = ClaudeHarness(claude_config)
        cmd = harness.build_execute_command("p", {"setting_sources": "user"})
        assert "--setting-sources" in cmd
        idx = cmd.index("--setting-sources")
        value = cmd[idx + 1]
        assert value == "user", (
            f"setting_sources='user' must emit '--setting-sources user' "
            f"(literal, no comma-list); got {value!r}"
        )

    def test_setting_sources_comma_list_preserved(self, claude_config):
        """A comma-separated sources list is forwarded verbatim."""
        harness = ClaudeHarness(claude_config)
        cmd = harness.build_execute_command(
            "p", {"setting_sources": "user,project"}
        )
        assert "--setting-sources" in cmd
        idx = cmd.index("--setting-sources")
        assert cmd[idx + 1] == "user,project"

    def test_setting_sources_false_disables_flag(self, claude_config):
        """Explicit setting_sources=False opts out — no flag emitted.

        Useful for callers that want to be loud about the opt-out rather
        than rely on the default behavior."""
        harness = ClaudeHarness(claude_config)
        cmd = harness.build_execute_command("p", {"setting_sources": False})
        assert "--setting-sources" not in cmd

    def test_setting_sources_none_disables_flag(self, claude_config):
        """setting_sources=None behaves the same as missing key."""
        harness = ClaudeHarness(claude_config)
        cmd = harness.build_execute_command("p", {"setting_sources": None})
        assert "--setting-sources" not in cmd

    def test_setting_sources_empty_string_disables_flag(self, claude_config):
        """An empty string is treated as no setting_sources (falsy)."""
        harness = ClaudeHarness(claude_config)
        cmd = harness.build_execute_command("p", {"setting_sources": ""})
        assert "--setting-sources" not in cmd

    def test_setting_sources_non_string_disables_flag(self, claude_config):
        """A non-string value (int / list / dict) is treated as no flag.

        Defensive: the harness only honors string values; a list of
        sources passed by mistake won't be stringified into a broken
        CLI flag."""
        harness = ClaudeHarness(claude_config)
        cmd = harness.build_execute_command("p", {"setting_sources": ["user", "project"]})
        assert "--setting-sources" not in cmd

    def test_setting_sources_already_in_execute_args_idempotent(self, claude_config):
        """If execute_args already carries --setting-sources, do not duplicate.

        Operators may pin --setting-sources via AgentConfig.execute_args
        (e.g. for a board-wide override). The harness must respect the
        operator's choice and not append a second --setting-sources flag.
        """
        harness = ClaudeHarness(
            AgentConfig(
                cli_command="claude",
                execute_args="--setting-sources user,project",
            )
        )
        cmd = harness.build_execute_command("p", {"setting_sources": "user"})
        occurrences = cmd.count("--setting-sources")
        assert occurrences == 1, (
            f"Setting --setting-sources in execute_args must be idempotent — "
            f"got {occurrences} occurrences in cmd; harness must not duplicate "
            f"the operator-supplied flag."
        )
        idx = cmd.index("--setting-sources")
        assert cmd[idx + 1] == "user,project", (
            "execute_args value must take precedence over context['setting_sources']"
        )

    def test_model_appended_when_in_context(self, claude_config):
        harness = ClaudeHarness(claude_config)
        cmd = harness.build_execute_command("p", {"model": "claude-sonnet-4-5"})
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "claude-sonnet-4-5"

    def test_no_model_flag_when_not_in_context(self, claude_config):
        harness = ClaudeHarness(claude_config)
        cmd = harness.build_execute_command("p", {})
        assert "--model" not in cmd

    def test_setting_sources_emitted_with_model(self, claude_config):
        """Reviewer path: --setting-sources and --model coexist."""
        harness = ClaudeHarness(claude_config)
        cmd = harness.build_execute_command(
            "p", {"model": "claude-opus-4-6", "setting_sources": "user"}
        )
        assert "--setting-sources" in cmd
        assert cmd[cmd.index("--setting-sources") + 1] == "user"
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "claude-opus-4-6"

    def test_setting_sources_strip_whitespace(self, claude_config):
        """Leading/trailing whitespace in setting_sources is stripped."""
        harness = ClaudeHarness(claude_config)
        cmd = harness.build_execute_command("p", {"setting_sources": "  user  "})
        assert "--setting-sources" in cmd
        idx = cmd.index("--setting-sources")
        assert cmd[idx + 1] == "user"

    def test_setting_sources_whitespace_only_no_flag(self, claude_config):
        """Whitespace-only setting_sources is treated as no flag."""
        harness = ClaudeHarness(claude_config)
        cmd = harness.build_execute_command("p", {"setting_sources": "   "})
        assert "--setting-sources" not in cmd


class TestClaudeBuildInteractiveCommand:
    """Interactive path (odin plan / tmux) honors the same opt-in contract."""

    def test_no_setting_sources_by_default(self, claude_config):
        harness = ClaudeHarness(claude_config)
        cmd = harness.build_interactive_command("/tmp/sysprompt.md", {})
        assert "--setting-sources" not in cmd

    def test_setting_sources_from_context_emitted(self, claude_config):
        harness = ClaudeHarness(claude_config)
        cmd = harness.build_interactive_command(
            "/tmp/sysprompt.md", {"setting_sources": "user"}
        )
        assert "--setting-sources" in cmd
        idx = cmd.index("--setting-sources")
        assert cmd[idx + 1] == "user"

    def test_setting_sources_already_in_execute_args_idempotent(self, claude_config):
        harness = ClaudeHarness(
            AgentConfig(
                cli_command="claude",
                execute_args="--setting-sources user,project",
            )
        )
        cmd = harness.build_interactive_command(
            "/tmp/sysprompt.md", {"setting_sources": "user"}
        )
        assert cmd.count("--setting-sources") == 1

    def test_model_passes_through(self, claude_config):
        harness = ClaudeHarness(claude_config)
        cmd = harness.build_interactive_command(
            "/tmp/sysprompt.md", {"model": "claude-opus-4-6"}
        )
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "claude-opus-4-6"

    def test_setting_sources_with_model(self, claude_config):
        """Interactive path: setting_sources and model coexist (parity check)."""
        harness = ClaudeHarness(claude_config)
        cmd = harness.build_interactive_command(
            "/tmp/sysprompt.md",
            {"model": "claude-opus-4-6", "setting_sources": "user"},
        )
        assert "--setting-sources" in cmd
        assert cmd[cmd.index("--setting-sources") + 1] == "user"
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "claude-opus-4-6"