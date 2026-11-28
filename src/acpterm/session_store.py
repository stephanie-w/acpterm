from __future__ import annotations

from contextlib import suppress
from datetime import datetime, UTC
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


DEFAULT_SESSION_NAME = "default"
SESSIONS_FILE = Path.home() / ".acpterm" / "sessions.json"


def _read() -> dict[str, Any]:
    if not SESSIONS_FILE.exists():
        return {}
    try:
        data = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(
            f"Warning: Failed to read session store {SESSIONS_FILE}: {e}. Ignoring it.",
            file=sys.stderr,
        )
        return {}
    if not isinstance(data, dict):
        print(
            f"Warning: Session store {SESSIONS_FILE} does not contain a JSON object."
            " Ignoring it.",
            file=sys.stderr,
        )
        return {}
    return data


def _write(data: dict[str, Any]) -> None:
    SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=SESSIONS_FILE.parent,
            delete=False,
        ) as tmp_file:
            json.dump(data, tmp_file, indent=2)
            tmp_path = Path(tmp_file.name)
        os.replace(tmp_path, SESSIONS_FILE)
    except BaseException:
        if tmp_path is not None:
            with suppress(OSError):
                tmp_path.unlink()
        raise


def _make_key(agent_name: str, cwd: str, name: str) -> str:
    return f"{agent_name}:{cwd}:{name}"


def get_entry(
    agent_name: str, cwd: str, name: str = DEFAULT_SESSION_NAME
) -> dict[str, Any] | None:
    data = _read()
    entry = data.get(_make_key(agent_name, cwd, name))
    return entry if isinstance(entry, dict) else None


def get(agent_name: str, cwd: str, name: str = DEFAULT_SESSION_NAME) -> str | None:
    entry = get_entry(agent_name, cwd, name)
    if not entry:
        return None
    session_id = entry.get("session_id")
    return session_id if isinstance(session_id, str) and session_id else None


def save(
    agent_name: str, cwd: str, session_id: str, name: str = DEFAULT_SESSION_NAME
) -> None:
    data = _read()
    key = _make_key(agent_name, cwd, name)
    existing = data.get(key)
    if not isinstance(existing, dict):
        existing = {}
    now_iso = datetime.now(UTC).isoformat()
    created_at = existing.get("created_at", now_iso)
    data[key] = {
        "agent_name": agent_name,
        "cwd": cwd,
        "name": name,
        "session_id": session_id,
        "created_at": created_at,
        "last_used_at": now_iso,
    }
    _write(data)


def remove(agent_name: str, cwd: str, name: str = DEFAULT_SESSION_NAME) -> bool:
    data = _read()
    key = _make_key(agent_name, cwd, name)
    if key in data:
        del data[key]
        _write(data)
        return True
    return False


def get_latest(agent_name: str, cwd: str) -> dict[str, Any] | None:
    data = _read()
    matching = [
        e
        for e in data.values()
        if isinstance(e, dict)
        and isinstance(e.get("session_id"), str)
        and e["session_id"]
        and e.get("agent_name") == agent_name
        and e.get("cwd") == cwd
    ]
    if not matching:
        return None
    matching.sort(
        key=lambda e: str(e.get("last_used_at") or e.get("created_at", "")),
        reverse=True,
    )
    return matching[0]


def list_sessions(
    agent_name: str | None = None, cwd: str | None = None
) -> list[dict[str, Any]]:
    data = _read()
    result = [e for e in data.values() if isinstance(e, dict)]
    if agent_name:
        result = [e for e in result if e.get("agent_name") == agent_name]
    if cwd:
        result = [e for e in result if e.get("cwd") == cwd]
    return result
