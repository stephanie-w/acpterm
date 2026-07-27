"""Environment variable authentication provider."""

from __future__ import annotations

import os
from typing import Any

from .base import AuthProvider


class EnvAuthProvider(AuthProvider):
    """AuthProvider that resolves tokens from generic ACPTERM environment variables."""

    async def get_token(
        self, agent_name: str, params: dict[str, Any], verbose: bool = False
    ) -> dict[str, Any] | None:
        agent_env_key = f"ACPTERM_{agent_name.upper().replace('-', '_')}_TOKEN"
        env_token = os.environ.get(agent_env_key) or os.environ.get(
            "ACPTERM_AUTH_TOKEN"
        )
        if not env_token:
            return None

        result: dict[str, Any] = {
            "accessToken": env_token,
            "tokenType": "Bearer",
            "expiresAt": os.environ.get("ACPTERM_EXPIRES_AT", "2099-12-31T23:59:59Z"),
        }
        if profile_arn := (
            os.environ.get(
                f"ACPTERM_{agent_name.upper().replace('-', '_')}_PROFILE_ARN"
            )
            or os.environ.get("ACPTERM_PROFILE_ARN")
            or os.environ.get("AWS_PROFILE_ARN")
        ):
            result["profileArn"] = profile_arn

        if region := os.environ.get("AWS_REGION") or os.environ.get("REGION"):
            result["region"] = region

        return result
