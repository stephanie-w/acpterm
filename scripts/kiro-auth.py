"""Extract Kiro OIDC access token from local storage.

Reads the Kiro SQLite auth DB (~/.local/share/kiro-cli/data.sqlite3)
and scans log files (~/.kiro/logs) for the profile ARN.

Outputs JSON to stdout for use as a token_command in ~/.acpterm/config.json:

    {
      "token_commands": {
        "kiro": "python3 path/to/scripts/kiro-auth.py"
      }
    }

Optional: --verbose for diagnostic output on stderr.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import sys


DB_PATH = Path.home() / ".local" / "share" / "kiro-cli" / "data.sqlite3"
LOGS_DIR = Path.home() / ".kiro" / "logs"
TOKEN_KEY = "kirocli:odic:token"  # noqa: S105 — DB lookup key, not a secret
_PROFILE_ARN_RE = re.compile(r"arn:aws:(?:codewhisperer|sso|iam):[a-zA-Z0-9:\-/]+")


def _log(verbose: bool, message: str) -> None:
    if verbose:
        print(message, file=sys.stderr)


def _extract_profile_arn() -> str | None:
    if not LOGS_DIR.exists():
        return None
    try:
        log_files = sorted(
            LOGS_DIR.glob("**/kiro.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return None
    for log_file in log_files:
        try:
            text = log_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        matches = _PROFILE_ARN_RE.findall(text)
        if matches:
            return matches[-1]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract Kiro OIDC access token.")
    parser.add_argument(
        "--verbose", action="store_true", help="Verbose diagnostics on stderr"
    )
    args = parser.parse_args()
    verbose: bool = args.verbose

    if not DB_PATH.exists():
        _log(verbose, f"Kiro token DB not found at {DB_PATH}")
        sys.exit(1)

    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT value FROM auth_kv WHERE key = ?", (TOKEN_KEY,)
        ).fetchone()
        conn.close()
    except sqlite3.Error as exc:
        _log(verbose, f"Could not read Kiro token DB: {exc}")
        sys.exit(1)

    if row is None:
        _log(verbose, f"Token key '{TOKEN_KEY}' not found in Kiro DB")
        sys.exit(1)

    try:
        token_data: dict[str, object] = json.loads(row[0])
    except json.JSONDecodeError as exc:
        _log(verbose, f"Token JSON in Kiro DB is not valid: {exc}")
        sys.exit(1)

    expires_at_str = token_data.get("expires_at")
    if expires_at_str and isinstance(expires_at_str, str):
        try:
            expiry = datetime.datetime.fromisoformat(expires_at_str)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=datetime.UTC)
            if expiry < datetime.datetime.now(datetime.UTC):
                _log(
                    verbose,
                    "Kiro token has expired. Run 'kiro-cli logout && kiro-cli login' to refresh.",
                )
        except ValueError:
            pass

    access_token = token_data.get("access_token") or token_data.get("accessToken")
    if not access_token:
        _log(verbose, "'access_token' field missing in Kiro token DB")
        sys.exit(1)

    result: dict[str, object] = {
        "accessToken": access_token,
        "tokenType": token_data.get("token_type")
        or token_data.get("tokenType", "Bearer"),
        "expiresAt": expires_at_str or "2099-12-31T23:59:59Z",
    }

    profile_arn = (
        token_data.get("profile_arn")
        or token_data.get("profileArn")
        or _extract_profile_arn()
        or os.environ.get("KIRO_PROFILE_ARN")
        or os.environ.get("AWS_PROFILE_ARN")
        or os.environ.get("ACPTERM_PROFILE_ARN")
    )
    if profile_arn and isinstance(profile_arn, str):
        result["profileArn"] = profile_arn

    region = token_data.get("region")
    if not region:
        region = os.environ.get("AWS_REGION")
    if region and isinstance(region, str):
        result["region"] = region

    start_url = token_data.get("start_url") or token_data.get("startUrl")
    if start_url and isinstance(start_url, str):
        result["startUrl"] = start_url

    json.dump(result, sys.stdout)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
