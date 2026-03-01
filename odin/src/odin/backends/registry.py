"""Backend registration and factory."""

from typing import Any, Dict, Type

from odin.backends.base import BoardBackend

BACKEND_REGISTRY: Dict[str, Type[BoardBackend]] = {}


def register_backend(name: str):
    """Decorator to register a backend class."""

    def decorator(cls: Type[BoardBackend]):
        BACKEND_REGISTRY[name] = cls
        return cls

    return decorator


def get_backend(name: str, **kwargs: Any) -> BoardBackend:
    """Instantiate a backend by name."""
    _import_all_backends()
    if name not in BACKEND_REGISTRY:
        raise ValueError(
            f"Unknown backend: {name}. Available: {list(BACKEND_REGISTRY.keys())}"
        )
    return BACKEND_REGISTRY[name](**kwargs)


def _import_all_backends():
    """Import all backend modules to trigger registration."""
    from odin.backends import local  # noqa: F401
    from odin.backends import taskit  # noqa: F401
