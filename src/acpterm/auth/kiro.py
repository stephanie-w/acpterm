"""Kiro-specific authentication provider."""

from __future__ import annotations

import contextlib
import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any

from .base import AuthProvider


KIRO_DB_PATH = Path.home() / ".local" / "share" / "kiro-cli" / "data.sqlite3"
KIRO_LOGS_DIR = Path.home() / ".kiro" / "logs"
KIRO_TOKEN_KEY = "kirocli:odic:token"  # noqa: S105 — DB lookup key, not secret credential

_PROFILE_ARN_PATTERN = re.compile(r"arn:aws:(?:codewhisperer|sso|iam):[a-zA-Z0-9:\-/]+")


def _extract_profile_arn_from_kiro_logs() -> str | None:
    """Scan Kiro log files in ~/.kiro/logs for a resolved profileArn."""
    if not KIRO_LOGS_DIR.exists():
        return None

    with contextlib.suppress(Exception):
        log_files = sorted(
            KIRO_LOGS_DIR.glob("**/kiro.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for log_file in log_files:
            text = log_file.read_text(encoding="utf-8", errors="ignore")
            matches = _PROFILE_ARN_PATTERN.findall(text)
            if matches:
                return matches[-1]

    return None


class KiroAuthProvider(AuthProvider):
    """AuthProvider that extracts OIDC authentication tokens from local Kiro SQLite storage and runtime logs."""

    async def get_token(
        self, agent_name: str, params: dict[str, Any], verbose: bool = False
    ) -> dict[str, Any] | None:
        if agent_name.lower() not in ("kiro", "kiro-cli"):
            return None

        if not KIRO_DB_PATH.exists():
            if verbose:
                print(
                    f"[yellow]Warning: Kiro token DB not found at {KIRO_DB_PATH}[/yellow]",
                    file=sys.stderr,
                )
            return None

        try:
            with sqlite3.connect(f"file:{KIRO_DB_PATH}?mode=ro", uri=True) as conn:
                row = conn.execute(
                    "SELECT value FROM auth_kv WHERE key = ?", (KIRO_TOKEN_KEY,)
                ).fetchone()
        except sqlite3.Error as exc:
            if verbose:
                print(
                    f"[yellow]Warning: Could not read Kiro token DB: {exc}[/yellow]",
                    file=sys.stderr,
                )
            return None

        if row is None:
            if verbose:
                print(
                    f"[yellow]Warning: Token key '{KIRO_TOKEN_KEY}' not found in Kiro DB[/yellow]",
                    file=sys.stderr,
                )
            return None

        try:
            token_data: dict[str, Any] = json.loads(row[0])
        except json.JSONDecodeError as exc:
            if verbose:
                print(
                    f"[yellow]Warning: Could not parse Kiro token JSON: {exc}[/yellow]",
                    file=sys.stderr,
                )
            return None

        expires_at = token_data.get("expires_at")
        if expires_at:
            try:
                expiry = datetime.datetime.fromisoformat(expires_at)
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=datetime.UTC)
                if expiry < datetime.datetime.now(datetime.UTC):
                    print(
                        "[yellow]Warning: Kiro token has expired. Run 'kiro-cli logout && kiro-cli login' to refresh.[/yellow]",
                        file=sys.stderr,
                    )
            except ValueError:
                pass

        access_token = token_data.get("access_token") or token_data.get("accessToken")
        if not access_token:
            if verbose:
                print(
                    "[yellow]Warning: 'access_token' field missing in Kiro token DB[/yellow]",
                    file=sys.stderr,
                )
            return None

        result: dict[str, Any] = {
            "accessToken": access_token,
            "tokenType": token_data.get("token_type")
            or token_data.get("tokenType", "Bearer"),
            "expiresAt": expires_at or "2099-12-31T23:59:59Z",
        }

        # Resolve profileArn from DB -> Kiro logs -> Environment variables
        profile_arn = (
            token_data.get("profile_arn")
            or token_data.get("profileArn")
            or _extract_profile_arn_from_kiro_logs()
            or os.environ.get("KIRO_PROFILE_ARN")
            or os.environ.get("AWS_PROFILE_ARN")
            or os.environ.get("ACPTERM_PROFILE_ARN")
        )
        if profile_arn:
            result["profileArn"] = profile_arn

        if region := token_data.get("region") or os.environ.get("AWS_REGION"):
            result["region"] = region

        if start_url := token_data.get("start_url") or token_data.get("startUrl"):
            result["startUrl"] = start_url

        return result
