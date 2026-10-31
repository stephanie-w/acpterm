"""Authentication module registry and dispatcher for acpterm."""

from __future__ import annotations

from typing import Any

from .base import AuthProvider
from .command import CommandAuthProvider
from .env import EnvAuthProvider


_PROVIDERS: list[AuthProvider] = [
    EnvAuthProvider(),
    CommandAuthProvider(),
]


def has_auth_configured(agent_name: str) -> bool:
    """Check if any registered AuthProvider has configuration for this agent."""
    return any(provider.has_config(agent_name) for provider in _PROVIDERS)


async def resolve_auth_token(
    agent_name: str,
    method: str,
    params: dict[str, Any],
    verbose: bool = False,
) -> dict[str, Any]:
    """Resolve authentication token using registered AuthProvider modules.

    Iterates through registered providers (Env -> Command -> Kiro -> Fallback)
    and returns the first matching non-None token dictionary.
    """
    for provider in _PROVIDERS:
        result = await provider.get_token(
            agent_name=agent_name, params=params, verbose=verbose
        )
        if result is not None:
            return result

    return {}


__all__ = ["AuthProvider", "resolve_auth_token"]
