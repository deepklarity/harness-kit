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


def _resolve_sandbox_mode(config: AgentConfig) -> str:
    """Effective sandbox mode, honoring the legacy ``run_in_forkd`` flag.

    Precedence: explicit ``sandbox_mode`` wins; when it's unset ("none"), a legacy
    ``run_in_forkd: true`` still resolves to forkd so existing configs keep working.
    """
    mode = (getattr(config, "sandbox_mode", None) or "none").lower()
    if mode in ("none", "") and config.run_in_forkd:
        return "forkd"
    return mode


def get_harness(name: str, config: AgentConfig) -> BaseHarness:
    """Instantiate a harness by name, optionally wrapped in a sandbox decorator."""
    if name not in HARNESS_REGISTRY:
        raise ValueError(
            f"Unknown harness: {name}. Available: {list(HARNESS_REGISTRY.keys())}"
        )
    harness = HARNESS_REGISTRY[name](config)
    mode = _resolve_sandbox_mode(config)
    if mode == "microsandbox":
        from odin.harnesses.microsandbox import MicrosandboxHarness
        return MicrosandboxHarness(name, harness, config)
    if mode == "forkd":
        from odin.harnesses.forkd import ForkdHarness
        return ForkdHarness(name, harness, config)
    return harness


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
        agy,
        claude,
        codex,
        gemini,
        glm,
        minimax,
        mock,
    )


_import_all_harnesses()
