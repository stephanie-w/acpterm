"""Command-based authentication provider."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from ..config import Config
from .base import AuthProvider


class CommandAuthProvider(AuthProvider):
    """AuthProvider that executes custom token_commands from ~/.acpterm/config.json."""

    async def get_token(
        self, agent_name: str, params: dict[str, Any], verbose: bool = False
    ) -> dict[str, Any] | None:
        config = Config.load()
        token_cmd = config.get_token_command(agent_name)
        if not token_cmd:
            return None

        try:
            proc = await asyncio.create_subprocess_shell(
                token_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            output = stdout.decode().strip()
            if not output:
                return None

            if output.startswith("{"):
                with contextlib.suppress(Exception):
                    payload = json.loads(output)
                    if isinstance(payload, dict):
                        normalized = {
                            "accessToken": payload.get("accessToken")
                            or payload.get("access_token", ""),
                            "tokenType": payload.get("tokenType")
                            or payload.get("token_type", "Bearer"),
                            "expiresAt": payload.get("expiresAt")
                            or payload.get("expires_at", "2099-12-31T23:59:59Z"),
                        }
                        if profile_arn := (
                            payload.get("profileArn") or payload.get("profile_arn")
                        ):
                            normalized["profileArn"] = profile_arn
                        if region := payload.get("region"):
                            normalized["region"] = region
                        if start_url := (
                            payload.get("startUrl") or payload.get("start_url")
                        ):
                            normalized["startUrl"] = start_url
                        return normalized

            return {
                "accessToken": output,
                "tokenType": "Bearer",
                "expiresAt": "2099-12-31T23:59:59Z",
            }
        except Exception:
            return None
