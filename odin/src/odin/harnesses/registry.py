"""Harness registration and factory."""

from typing import Dict, Type

from odin.harnesses.base import BaseHarness
from odin.models import AgentConfig

HARNESS_REGISTRY: Dict[str, Type[BaseHarness]] = {}


def register_harness(name: str):
    """Decorator to register a harness class."""

    def decorator(cls: Type[BaseHarness]):
        HARNESS_REGISTRY[name] = cls
        return cls

    return decorator


def get_harness(name: str, config: AgentConfig) -> BaseHarness:
    """Instantiate a harness by name."""
    if name not in HARNESS_REGISTRY:
        raise ValueError(
            f"Unknown harness: {name}. Available: {list(HARNESS_REGISTRY.keys())}"
        )
    return HARNESS_REGISTRY[name](config)


def get_all_harnesses(
    configs: Dict[str, AgentConfig],
) -> Dict[str, BaseHarness]:
    """Instantiate all enabled harnesses from config."""
    harnesses = {}
    for name, cfg in configs.items():
        if cfg.enabled:
            try:
                harnesses[name] = get_harness(name, cfg)
            except ValueError:
                pass
    return harnesses


def _import_all_harnesses():
    """Import all harness modules to trigger registration."""
    from odin.harnesses import (  # noqa: F401
        claude,
        codex,
        gemini,
        glm,
        minimax,
        mock,
        qwen,
    )


_import_all_harnesses()
