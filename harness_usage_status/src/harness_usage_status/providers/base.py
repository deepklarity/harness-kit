from abc import ABC, abstractmethod
from typing import Optional

from harness_usage_status.models import UsageInfo, StatusInfo


class BaseProvider(ABC):
    """Base class for all provider integrations.

    Subclasses must implement get_usage() and get_status().
    Config is passed as a dict from the provider's YAML config section.
    """

    def __init__(self, config: dict):
        self.config = config

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider display name."""
        ...

    @abstractmethod
    async def get_usage(self) -> UsageInfo:
        """Fetch current usage/quota information."""
        ...

    @abstractmethod
    async def get_status(self) -> StatusInfo:
        """Fetch current provider status/health."""
        ...
