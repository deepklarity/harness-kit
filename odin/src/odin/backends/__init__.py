"""Board backend abstraction layer."""

from odin.backends.base import BoardBackend  # noqa: F401
from odin.backends.registry import get_backend, BACKEND_REGISTRY  # noqa: F401
