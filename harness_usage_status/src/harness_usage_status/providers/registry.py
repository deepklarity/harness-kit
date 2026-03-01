from typing import Dict, Type

from harness_usage_status.providers.base import BaseProvider

# Provider name -> class mapping. Providers register themselves on import.
PROVIDER_REGISTRY: Dict[str, Type[BaseProvider]] = {}


def register_provider(name: str):
    """Decorator to register a provider class."""
    def decorator(cls: Type[BaseProvider]):
        PROVIDER_REGISTRY[name] = cls
        return cls
    return decorator


def get_provider(name: str, config: dict) -> BaseProvider:
    """Instantiate a provider by name with its config."""
    if name not in PROVIDER_REGISTRY:
        raise ValueError(f"Unknown provider: {name}. Available: {list(PROVIDER_REGISTRY.keys())}")
    return PROVIDER_REGISTRY[name](config)


def get_all_providers(configs: Dict[str, dict]) -> Dict[str, BaseProvider]:
    """Instantiate all enabled providers from config."""
    providers = {}
    for name, cfg in configs.items():
        if cfg.get("enabled", True):
            try:
                providers[name] = get_provider(name, cfg)
            except ValueError:
                pass  # skip unknown providers
    return providers


def _import_all_providers():
    """Import all provider modules to trigger registration."""
    from harness_usage_status.providers import (  # noqa: F401
        claude_code,
        codex,
        gemini,
        minimax,
        glm,
        qwen,
    )


_import_all_providers()
