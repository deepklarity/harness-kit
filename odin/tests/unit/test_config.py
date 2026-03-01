"""Tests for config loading and defaults.

Tags: [io] + [simple] — YAML parsing, env var substitution, no LLM calls.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

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
        assert "qwen" in cfg.agents

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
        assert cfg.agents["qwen"].cost_tier == CostTier.LOW

    def test_default_cli_agents_enabled(self):
        cfg = _default_config("test")
        assert cfg.agents["minimax"].enabled is True
        assert cfg.agents["minimax"].cli_command == "kilo"
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

    def test_empty_yaml_returns_defaults(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("")
        cfg = load_config(str(config_file))
        assert cfg.base_agent == "claude"  # default

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


# ── Config hierarchy ──────────────────────────────────────────────────


class TestConfigHierarchy:
    def test_explicit_path_takes_priority(self, tmp_path):
        explicit = tmp_path / "explicit.yaml"
        explicit.write_text("base_agent: qwen\nagents:\n  qwen:\n    enabled: true")

        local = tmp_path / ".odin" / "config.yaml"
        local.parent.mkdir(parents=True)
        local.write_text("base_agent: gemini\nagents:\n  gemini:\n    enabled: true")

        cfg = load_config(str(explicit))
        assert cfg.base_agent == "qwen"

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
            {"agent": "qwen", "model": "qwen3-coder"},
            {"agent": "gemini", "model": "gemini-2.5-flash"},
        ]
        result = _parse_model_routing(raw)
        assert len(result) == 2
        assert result[0].agent == "qwen"
        assert result[1].model == "gemini-2.5-flash"

    def test_empty_returns_empty(self):
        assert _parse_model_routing(None) == []
        assert _parse_model_routing([]) == []

    def test_invalid_entries_skipped(self):
        raw = [
            {"agent": "qwen", "model": "qwen3"},
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
