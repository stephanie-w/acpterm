"""Base AuthProvider interface for acpterm extension authentication."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AuthProvider(ABC):
    """Abstract base class for agent extension authentication providers."""

    @abstractmethod
    async def get_token(
        self, agent_name: str, params: dict[str, Any], verbose: bool = False
    ) -> dict[str, Any] | None:
        """Fetch or construct authentication token response dictionary for the agent.

        Returns:
            dict[str, Any] with token fields (e.g. accessToken, expiresAt, profileArn),
            or None if this provider cannot fulfill the request.
        """
        ...
