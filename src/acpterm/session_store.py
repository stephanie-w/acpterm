from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_SESSION_NAME = "default"
SESSIONS_FILE = Path.home() / ".acpterm" / "sessions.json"


def _ensure_file() -> Path:
    SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not SESSIONS_FILE.exists():
        SESSIONS_FILE.write_text("{}")
    return SESSIONS_FILE


def _read() -> dict[str, Any]:
    path = _ensure_file()
    return json.loads(path.read_text())


def _write(data: dict[str, Any]) -> None:
    path = _ensure_file()
    path.write_text(json.dumps(data, indent=2))


def _make_key(agent_name: str, cwd: str, name: str) -> str:
    return f"{agent_name}:{cwd}:{name}"


def get_entry(
    agent_name: str, cwd: str, name: str = DEFAULT_SESSION_NAME
) -> dict[str, Any] | None:
    data = _read()
    return data.get(_make_key(agent_name, cwd, name))


def get(agent_name: str, cwd: str, name: str = DEFAULT_SESSION_NAME) -> str | None:
    data = _read()
    entry = data.get(_make_key(agent_name, cwd, name))
    return entry["session_id"] if entry else None


def save(
    agent_name: str, cwd: str, session_id: str, name: str = DEFAULT_SESSION_NAME
) -> None:
    data = _read()
    key = _make_key(agent_name, cwd, name)
    data[key] = {
        "agent_name": agent_name,
        "cwd": cwd,
        "name": name,
        "session_id": session_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
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


def list_sessions(
    agent_name: str | None = None, cwd: str | None = None
) -> list[dict[str, Any]]:
    data = _read()
    result = list(data.values())
    if agent_name:
        result = [e for e in result if e["agent_name"] == agent_name]
    if cwd:
        result = [e for e in result if e["cwd"] == cwd]
    return result
