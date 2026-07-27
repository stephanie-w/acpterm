"""Authentication module registry and dispatcher for acpterm."""

from __future__ import annotations

from typing import Any

from .base import AuthProvider
from .command import CommandAuthProvider
from .env import EnvAuthProvider
from .kiro import KiroAuthProvider


_PROVIDERS: list[AuthProvider] = [
    EnvAuthProvider(),
    CommandAuthProvider(),
    KiroAuthProvider(),
]


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
