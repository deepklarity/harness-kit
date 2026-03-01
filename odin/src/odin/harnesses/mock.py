"""Mock harness for testing the full orchestrator pipeline without LLM calls.

Returns a canned TaskResult with realistic metrics. Useful for:
- Testing comment composition and posting to TaskIt
- Verifying actor identity flow end-to-end
- Running odin exec --mock to exercise the full pipeline
"""

import random
import time

from odin.harnesses.base import BaseHarness
from odin.harnesses.registry import register_harness
from odin.models import AgentConfig, TaskResult


@register_harness("mock")
class MockHarness(BaseHarness):
    """Harness that returns canned results without calling any external service."""

    def __init__(self, config: AgentConfig):
        super().__init__(config)

    @property
    def name(self) -> str:
        return "mock"

    async def execute(self, prompt: str, context: dict) -> TaskResult:
        """Return a successful TaskResult with realistic metrics."""
        # Simulate brief execution time
        duration_ms = random.uniform(500, 3000)
        input_tokens = random.randint(200, 2000)
        output_tokens = random.randint(100, 1000)

        output = (
            f"Mock execution completed for prompt ({len(prompt)} chars).\n"
            "-------ODIN-STATUS-------\n"
            "SUCCESS\n"
            "-------ODIN-SUMMARY-------\n"
            "Mock task completed successfully."
        )

        return TaskResult(
            success=True,
            output=output,
            duration_ms=round(duration_ms, 1),
            agent="mock",
            metadata={
                "usage": {
                    "total_tokens": input_tokens + output_tokens,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                }
            },
        )

    def build_execute_command(self, prompt: str, context: dict):
        return None  # Not a CLI harness

    async def is_available(self) -> bool:
        return True
