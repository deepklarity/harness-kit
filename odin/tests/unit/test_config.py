"""Tests for config loading and defaults.

Tags: [io] + [simple] — YAML parsing, env var substitution, no LLM calls.
"""

import logging
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from odin.config import (
    _default_config,
    _parse_model_routing,
    _parse_models,
    load_config,
)
from odin.models import CostTier, ModelRoute, OdinConfig


# ── Built-in defaults ─────────────────────────────────────────────────


class TestDefaultConfig:
    def test_default_config_has_agents(self):
        cfg = _default_config("test")
        assert "claude" in cfg.agents
        assert "gemini" in cfg.agents
        assert "minimax" in cfg.agents

    def test_default_config_base_agent(self):
        cfg = _default_config("test")
        assert cfg.base_agent == "claude"

    def test_default_config_model_routing(self):
        cfg = _default_config("test")
        assert len(cfg.model_routing) > 0
        assert all(isinstance(r, ModelRoute) for r in cfg.model_routing)

    def test_default_config_source(self):
        cfg = _default_config("my source")
        assert cfg.config_source == "my source"

    def test_default_agent_cost_tiers(self):
        cfg = _default_config("test")
        assert cfg.agents["claude"].cost_tier == CostTier.HIGH
        assert cfg.agents["gemini"].cost_tier == CostTier.LOW
        assert cfg.agents["minimax"].cost_tier == CostTier.LOW

    def test_default_cli_agents_enabled(self):
        cfg = _default_config("test")
        assert cfg.agents["minimax"].enabled is True
        assert cfg.agents["minimax"].cli_command == "opencode"
        assert cfg.agents["glm"].enabled is True
        assert cfg.agents["glm"].cli_command == "opencode"


# ── YAML loading ──────────────────────────────────────────────────────


class TestYAMLLoading:
    def test_load_from_yaml(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
base_agent: gemini
agents:
  gemini:
    cli_command: /usr/local/bin/gemini
    capabilities: [coding, writing]
    cost_tier: low
  claude:
    cli_command: claude
    capabilities: [reasoning]
    cost_tier: high
""")
        cfg = load_config(str(config_file))
        assert cfg.base_agent == "gemini"
        assert cfg.agents["gemini"].cli_command == "/usr/local/bin/gemini"
        assert cfg.agents["claude"].cost_tier == CostTier.HIGH

    def test_load_from_yaml_reads_base_model(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
base_agent: claude
base_model: claude-sonnet-4-6
agents:
  claude:
    enabled: true
""")
        cfg = load_config(str(config_file))
        assert cfg.base_agent == "claude"
        assert cfg.base_model == "claude-sonnet-4-6"

    def test_empty_yaml_returns_defaults(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("")
        cfg = load_config(str(config_file))
        assert cfg.base_agent == "claude"  # default

    def test_max_turns_defaults_none(self, tmp_path):
        """Unset max_turns leaves the agent loop bounded only by wall-clock."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("base_agent: claude\n")
        cfg = load_config(str(config_file))
        assert cfg.max_turns is None

    def test_max_turns_parsed_from_yaml(self, tmp_path):
        """Operators can set an agentic step budget in config."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("base_agent: claude\nmax_turns: 60\n")
        cfg = load_config(str(config_file))
        assert cfg.max_turns == 60

    def test_unknown_keys_ignored(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
base_agent: claude
future_feature: something_new
agents:
  claude:
    enabled: true
    unknown_field: whatever
""")
        cfg = load_config(str(config_file))
        assert cfg.base_agent == "claude"
        # unknown_field stored in extras
        assert cfg.agents["claude"].extras.get("unknown_field") == "whatever"

    def test_merge_agent_defaults(self, tmp_path):
        """Merge agent defaults to enabled with claude (Default First)."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("base_agent: claude")
        cfg = load_config(str(config_file))
        assert cfg.merge_agent is not None
        assert cfg.merge_agent.enabled is True
        assert cfg.merge_agent.agent == "claude"

    def test_merge_agent_parsed_from_yaml(self, tmp_path):
        """merge_agent section in YAML overrides defaults."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
base_agent: claude
merge_agent:
  enabled: false
  agent: glm
  model: glm-4
""")
        cfg = load_config(str(config_file))
        assert cfg.merge_agent.enabled is False
        assert cfg.merge_agent.agent == "glm"
        assert cfg.merge_agent.model == "glm-4"

    def test_advisor_defaults(self, tmp_path):
        """Advisor trial defaults to disabled (opt-in, Default First)."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("base_agent: claude")
        cfg = load_config(str(config_file))
        assert cfg.advisor is not None
        assert cfg.advisor.enabled is False
        assert cfg.advisor.max_consults == 2
        assert cfg.advisor.model == "claude-sonnet-5"
        assert cfg.advisor.trial_agents == ["glm", "minimax"]

    def test_advisor_parsed_from_yaml(self, tmp_path):
        """advisor section in YAML overrides defaults."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
base_agent: claude
advisor:
  enabled: true
  agent: claude
  model: claude-sonnet-5
  max_consults: 3
  trial_agents: [glm]
""")
        cfg = load_config(str(config_file))
        assert cfg.advisor.enabled is True
        assert cfg.advisor.max_consults == 3
        assert cfg.advisor.trial_agents == ["glm"]


# ── Config hierarchy ──────────────────────────────────────────────────


class TestConfigHierarchy:
    def test_explicit_path_takes_priority(self, tmp_path):
        explicit = tmp_path / "explicit.yaml"
        explicit.write_text("base_agent: minimax\nagents:\n  minimax:\n    enabled: true")

        local = tmp_path / ".odin" / "config.yaml"
        local.parent.mkdir(parents=True)
        local.write_text("base_agent: gemini\nagents:\n  gemini:\n    enabled: true")

        cfg = load_config(str(explicit))
        assert cfg.base_agent == "minimax"

    def test_no_config_uses_defaults(self, tmp_path):
        with patch("odin.config.LOCAL_CONFIG_PATH", tmp_path / "nonexistent_local.yaml"), \
             patch("odin.config.GLOBAL_CONFIG_PATH", tmp_path / "nonexistent_global.yaml"):
            cfg = load_config()
        assert "defaults" in cfg.config_source


# ── Env var substitution ──────────────────────────────────────────────


class TestEnvVarSubstitution:
    def test_api_key_from_env(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
agents:
  minimax:
    api_key: ${MINIMAX_API_KEY}
    enabled: true
""")
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "secret123"}):
            cfg = load_config(str(config_file))
        assert cfg.agents["minimax"].api_key == "secret123"

    def test_missing_env_var_returns_none(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
agents:
  minimax:
    api_key: ${NONEXISTENT_VAR}
    enabled: true
""")
        with patch.dict(os.environ, {}, clear=True):
            # Remove the var if it exists
            os.environ.pop("NONEXISTENT_VAR", None)
            cfg = load_config(str(config_file))
        assert cfg.agents["minimax"].api_key is None


class TestForcedProviderEnv:
    def test_forced_provider_uses_default_model(self):
        with patch.dict(os.environ, {"FORCED_BASE_PROVIDER": "gemini", "FORCED_BASE_MODEL": ""}, clear=False), \
             patch("odin.forced_provider.shutil.which", return_value="/usr/bin/gemini"):
            cfg = _default_config("test")
        assert cfg.forced_base_provider == "gemini"
        assert cfg.forced_base_model == "gemini-3-flash-preview"

    def test_forced_provider_uses_pinned_model(self):
        with patch.dict(
            os.environ,
            {"FORCED_BASE_PROVIDER": "gemini", "FORCED_BASE_MODEL": "gemini-3.1-pro-preview"},
            clear=False,
        ), patch("odin.forced_provider.shutil.which", return_value="/usr/bin/gemini"):
            cfg = _default_config("test")
        assert cfg.forced_base_provider == "gemini"
        assert cfg.forced_base_model == "gemini-3.1-pro-preview"

    def test_invalid_forced_provider_raises(self):
        with patch.dict(os.environ, {"FORCED_BASE_PROVIDER": "claude", "FORCED_BASE_MODEL": ""}, clear=False):
            with pytest.raises(RuntimeError, match="FORCED_BASE_PROVIDER"):
                _default_config("test")


# ── Model parsing helpers ─────────────────────────────────────────────


class TestParseModels:
    def test_list_format(self):
        result = _parse_models(["model-a", "model-b"])
        assert result == {"model-a": "", "model-b": ""}

    def test_dict_format(self):
        result = _parse_models({"model-a": "fast", "model-b": None})
        assert result == {"model-a": "fast", "model-b": ""}

    def test_invalid_returns_empty(self):
        assert _parse_models("not a list or dict") == {}
        assert _parse_models(None) == {}


class TestParseModelRouting:
    def test_valid_list(self):
        raw = [
            {"agent": "minimax", "model": "minimax-coding-plan/MiniMax-M2.7"},
            {"agent": "gemini", "model": "gemini-2.5-flash"},
        ]
        result = _parse_model_routing(raw)
        assert len(result) == 2
        assert result[0].agent == "minimax"
        assert result[1].model == "gemini-2.5-flash"

    def test_empty_returns_empty(self):
        assert _parse_model_routing(None) == []
        assert _parse_model_routing([]) == []

    def test_invalid_entries_skipped(self):
        raw = [
            {"agent": "nonexistent", "model": "model-x"},
            {"invalid": True},
            "not a dict",
        ]
        result = _parse_model_routing(raw)
        assert len(result) == 1


# ── OdinConfig methods ────────────────────────────────────────────────


class TestOdinConfigMethods:
    def test_enabled_agents(self):
        from odin.models import AgentConfig

        cfg = OdinConfig(
            agents={
                "a": AgentConfig(enabled=True),
                "b": AgentConfig(enabled=False),
                "c": AgentConfig(enabled=True),
            }
        )
        enabled = cfg.enabled_agents()
        assert "a" in enabled
        assert "c" in enabled
        assert "b" not in enabled


class TestOdinConfigMcps:
    def test_mcps_default_is_all_three(self):
        cfg = OdinConfig()
        assert cfg.mcps == ["taskit", "mobile", "chrome-devtools"]

    def test_mcps_custom_value(self):
        cfg = OdinConfig(mcps=["taskit", "mobile"])
        assert cfg.mcps == ["taskit", "mobile"]

    def test_mcps_empty_list(self):
        cfg = OdinConfig(mcps=[])
        assert cfg.mcps == []

    def test_mcps_mobile_only(self):
        cfg = OdinConfig(mcps=["mobile"])
        assert cfg.mcps == ["mobile"]

    def test_mcps_parsed_from_yaml(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
base_agent: claude
mcps:
  - taskit
  - mobile
agents:
  claude:
    cli_command: claude
""")
        cfg = load_config(str(config_file))
        assert cfg.mcps == ["taskit", "mobile"]

    def test_mcps_defaults_when_missing_from_yaml(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
base_agent: claude
agents:
  claude:
    cli_command: claude
""")
        cfg = load_config(str(config_file))
        assert cfg.mcps == ["taskit", "mobile", "chrome-devtools"]


class TestForkdConfig:
    def test_loads_forkd_agent_fields(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
agents:
  codex:
    cli_command: codex
    run_in_forkd: true
    forkd_bin: /opt/forkd/bin/forkd
    forkd_kernel: /opt/forkd/vmlinux
    forkd_scripts_dir: /opt/forkd/scripts
    forkd_use_sudo: true
    forkd_mode: controller
    forkd_controller_url: http://127.0.0.1:8889
    forkd_snapshot_tag: odin-node-22-slim
    forkd_per_child_netns: true
    forkd_extra: [python3, ca-certificates, git]
    forkd_rootfs_size_mib: 8192
    forkd_mem_size_mib: 4096
    forkd_init_git: true
""")
        cfg = load_config(str(config_file))
        codex = cfg.agents["codex"]
        assert codex.run_in_forkd is True
        assert codex.forkd_bin == "/opt/forkd/bin/forkd"
        assert codex.forkd_kernel == "/opt/forkd/vmlinux"
        assert codex.forkd_scripts_dir == "/opt/forkd/scripts"
        assert codex.forkd_use_sudo is True
        assert codex.forkd_mode == "controller"
        assert codex.forkd_controller_url == "http://127.0.0.1:8889"
        assert codex.forkd_snapshot_tag == "odin-node-22-slim"
        assert codex.forkd_per_child_netns is True
        assert codex.forkd_extra == ["python3", "ca-certificates", "git"]
        assert codex.forkd_rootfs_size_mib == 8192
        assert codex.forkd_mem_size_mib == 4096
        assert ".odin" in codex.forkd_workspace_excludes
        assert ".gemini" in codex.forkd_workspace_excludes
        assert "opencode.json" in codex.forkd_workspace_excludes
        assert codex.forkd_init_git is True
        assert "run_in_forkd" not in codex.extras

    def test_loads_forkd_paths_from_env_placeholders(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FORKD_BIN", "/env/forkd")
        monkeypatch.setenv("FORKD_KERNEL", "/env/vmlinux")
        monkeypatch.setenv("FORKD_SCRIPTS_DIR", "/env/scripts")
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
agents:
  codex:
    cli_command: codex
    run_in_forkd: true
    forkd_bin: ${FORKD_BIN}
    forkd_kernel: ${FORKD_KERNEL}
    forkd_scripts_dir: ${FORKD_SCRIPTS_DIR}
""")

        cfg = load_config(str(config_file))
        codex = cfg.agents["codex"]
        assert codex.forkd_bin == "/env/forkd"
        assert codex.forkd_kernel == "/env/vmlinux"
        assert codex.forkd_scripts_dir == "/env/scripts"

    def test_gemini_env_key_does_not_force_api_key_auth(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret")
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
agents:
  gemini:
    cli_command: gemini
    run_in_forkd: true
""")

        cfg = load_config(str(config_file))
        gemini = cfg.agents["gemini"]
        assert gemini.api_key is None

    def test_gemini_can_explicitly_use_api_key_placeholder(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret")
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
agents:
  gemini:
    cli_command: gemini
    api_key: ${GEMINI_API_KEY}
    run_in_forkd: true
""")

        cfg = load_config(str(config_file))
        gemini = cfg.agents["gemini"]
        assert gemini.api_key == "gemini-secret"

    def test_auto_discovers_local_forkd_paths_when_env_missing(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FORKD_BIN", raising=False)
        monkeypatch.delenv("FORKD_KERNEL", raising=False)
        monkeypatch.delenv("FORKD_SCRIPTS_DIR", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / "forkd-poc" / "bin").mkdir(parents=True)
        (tmp_path / "forkd-poc" / "forkd" / "scripts").mkdir(parents=True)
        (tmp_path / "forkd-poc" / "bin" / "forkd").write_text("#!/bin/sh\n")
        (tmp_path / "forkd-poc" / "vmlinux").write_text("kernel\n")
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
agents:
  codex:
    cli_command: codex
    run_in_forkd: true
""")

        cfg = load_config(str(config_file))
        codex = cfg.agents["codex"]
        assert codex.forkd_bin == str(tmp_path / "forkd-poc" / "bin" / "forkd")
        assert codex.forkd_kernel == str(tmp_path / "forkd-poc" / "vmlinux")
        assert codex.forkd_scripts_dir == str(tmp_path / "forkd-poc" / "forkd" / "scripts")


class TestInitForkdAutomation:
    def test_apply_init_config_overlays_enables_codex_forkd(self, monkeypatch):
        from odin.cli import _apply_init_config_overlays

        monkeypatch.setenv("FORKD_BIN", "/opt/forkd/bin/forkd")
        monkeypatch.setenv("FORKD_KERNEL", "/opt/forkd/vmlinux")
        monkeypatch.setenv("FORKD_SCRIPTS_DIR", "/opt/forkd/scripts")

        data = _apply_init_config_overlays(
            {},
            board_id=25,
            base_url="http://localhost:9101",
            forkd=True,
            forkd_agent="codex",
        )

        assert data["base_agent"] == "codex"
        assert data["taskit"]["board_id"] == 25
        assert data["taskit"]["base_url"] == "http://localhost:9101"
        assert data["agents"]["codex"]["run_in_forkd"] is True
        assert data["agents"]["codex"]["forkd_mode"] == "controller"
        assert data["agents"]["codex"]["forkd_bin"] == "/opt/forkd/bin/forkd"
        assert data["agents"]["codex"]["forkd_kernel"] == "/opt/forkd/vmlinux"
        assert data["agents"]["codex"]["forkd_scripts_dir"] == "/opt/forkd/scripts"
        assert data["agents"]["codex"]["forkd_snapshot_tag"] == "odin-node22-4g-cli-browser"
        assert data["agents"]["codex"]["forkd_mem_size_mib"] == 4096
        assert data["agents"]["codex"]["forkd_per_child_netns"] is True

    def test_resolve_taskit_board_id_prefers_explicit_id(self):
        from odin.cli import _resolve_taskit_board_id

        assert _resolve_taskit_board_id(
            base_url=None,
            board_id="25",
            board_name=None,
            latest_board=False,
            cwd=None,
        ) == 25

    def test_resolve_taskit_board_id_by_latest(self, monkeypatch):
        from odin import cli

        monkeypatch.setattr(cli, "_fetch_taskit_boards", lambda base_url: [
            {"id": 12, "name": "old"},
            {"id": 25, "name": "new"},
        ])

        assert cli._resolve_taskit_board_id(
            base_url="http://localhost:9101",
            board_id=None,
            board_name=None,
            latest_board=True,
            cwd=None,
        ) == 25

    def test_resolve_taskit_board_id_by_name(self, monkeypatch):
        from odin import cli

        monkeypatch.setattr(cli, "_fetch_taskit_boards", lambda base_url: [
            {"id": 24, "name": "other"},
            {"id": 25, "name": "forkd codex smoke"},
        ])

        assert cli._resolve_taskit_board_id(
            base_url="http://localhost:9101",
            board_id=None,
            board_name="forkd codex smoke",
            latest_board=False,
            cwd=None,
        ) == 25


    def test_resolve_taskit_board_id_by_cwd_before_latest(self, monkeypatch, tmp_path):
        from odin import cli

        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.setattr(cli, "_fetch_taskit_boards", lambda base_url: [
            {"id": 24, "name": "newer-unrelated", "working_dir": str(tmp_path / "other")},
            {"id": 25, "name": "project", "working_dir": str(project)},
        ])

        assert cli._resolve_taskit_board_id(
            base_url="http://localhost:9101",
            board_id=None,
            board_name=None,
            latest_board=False,
            cwd=project,
        ) == 25
# ── Sample config (scaffold template) ─────────────────────────────────


class TestSampleConfig:
    """The sample config that `odin init` copies must not emit removed harnesses."""

    @property
    def _sample_path(self) -> Path:
        return Path(__file__).resolve().parent.parent.parent / "config" / "config.sample.yaml"

    def test_sample_config_agents_are_all_registered(self):
        """Every agent in the sample config must be a known, registered harness.

        Guards against the scaffold template shipping a stale entry for a
        harness removed from the registry — `odin init` would otherwise emit a
        config that planning immediately has to warn about.
        """
        from odin.harnesses.registry import HARNESS_REGISTRY

        raw = self._sample_path.read_text()
        data = yaml.safe_load(raw) or {}
        agents = data.get("agents", {})
        unknown = sorted(set(agents) - set(HARNESS_REGISTRY.keys()))
        assert not unknown, (
            f"config.sample.yaml contains unregistered harnesses: {unknown}. "
            f"`odin init` would scaffold a config planning cannot use."
        )

    def test_sample_config_has_valid_agents(self):
        """Sample config should still contain the expected set of known agents."""
        raw = self._sample_path.read_text()
        data = yaml.safe_load(raw) or {}
        agents = data.get("agents", {})
        for expected in ("claude", "codex", "gemini", "minimax", "glm"):
            assert expected in agents, f"Sample config missing expected agent: {expected}"


# ── Fail-soft on unknown harnesses ────────────────────────────────────


class TestUnknownHarnessFailSoft:
    """Config loading must skip unknown harnesses with a warning, not abort.

    When a configured agent names a harness odin doesn't know (e.g. a stale
    entry left over from a removed harness), the config loader should drop
    that agent and emit a clear warning, then return the remaining valid
    agents so planning/execution can proceed.
    """

    def test_unknown_agent_skipped_with_warning(self, tmp_path, caplog):
        """A config with a bogus agent should skip it and warn, not abort."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
base_agent: claude
agents:
  claude:
    cli_command: claude
    enabled: true
  bogus_agent:
    cli_command: bogus-cli
    enabled: true
""")
        with caplog.at_level(logging.WARNING, logger="odin.config"):
            cfg = load_config(str(config_file))

        # Valid agent survives
        assert "claude" in cfg.agents
        # Unknown agent was filtered out
        assert "bogus_agent" not in cfg.agents
        # A warning was logged mentioning the skipped agent
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("bogus_agent" in r.message for r in warnings), (
            f"Expected a warning mentioning 'bogus_agent', got: {[r.message for r in warnings]}"
        )

    def test_unknown_agent_does_not_block_valid_agents(self, tmp_path):
        """Multiple valid agents should all survive even when one is unknown."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
base_agent: claude
agents:
  claude:
    cli_command: claude
    enabled: true
  gemini:
    cli_command: gemini
    enabled: true
  stale_harness:
    cli_command: gone
    enabled: true
""")
        cfg = load_config(str(config_file))
        assert "claude" in cfg.agents
        assert "gemini" in cfg.agents
        assert "stale_harness" not in cfg.agents
