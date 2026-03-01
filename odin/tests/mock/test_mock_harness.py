"""Tests for the mock harness.

Tags: [mock] — no external services, no LLM.
"""

import asyncio

import pytest

from odin.harnesses.mock import MockHarness
from odin.harnesses.registry import HARNESS_REGISTRY
from odin.models import AgentConfig


class TestMockHarness:
    def test_registered_in_registry(self):
        assert "mock" in HARNESS_REGISTRY

    def test_execute_returns_success(self):
        harness = MockHarness(AgentConfig(enabled=True))
        result = asyncio.run(harness.execute("test prompt", {}))
        assert result.success is True
        assert result.duration_ms is not None
        assert result.duration_ms > 0
        assert "usage" in result.metadata
        assert result.metadata["usage"]["total_tokens"] > 0

    def test_execute_contains_odin_envelope(self):
        """Output includes ODIN-STATUS and ODIN-SUMMARY markers for the orchestrator."""
        harness = MockHarness(AgentConfig(enabled=True))
        result = asyncio.run(harness.execute("test prompt", {}))
        assert "-------ODIN-STATUS-------" in result.output
        assert "SUCCESS" in result.output
        assert "-------ODIN-SUMMARY-------" in result.output

    def test_is_available_always_true(self):
        harness = MockHarness(AgentConfig(enabled=True))
        assert asyncio.run(harness.is_available()) is True

    def test_build_execute_command_returns_none(self):
        """Mock is not a CLI harness."""
        harness = MockHarness(AgentConfig(enabled=True))
        assert harness.build_execute_command("prompt", {}) is None

    def test_token_usage_has_breakdown(self):
        harness = MockHarness(AgentConfig(enabled=True))
        result = asyncio.run(harness.execute("test", {}))
        usage = result.metadata["usage"]
        assert usage["input_tokens"] + usage["output_tokens"] == usage["total_tokens"]
