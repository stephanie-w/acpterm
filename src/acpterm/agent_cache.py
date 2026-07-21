from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CACHE_FILE = Path.home() / ".acpterm" / "agent_cache.json"
CACHE_TTL = timedelta(days=7)


def _read() -> dict[str, Any]:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {}


def _write(data: dict[str, Any]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=2))


def is_fresh(agent_name: str) -> bool:
    data = _read()
    entry = data.get(agent_name)
    if not entry:
        return False
    try:
        refreshed_at = datetime.fromisoformat(entry["refreshed_at"])
    except ValueError, KeyError:
        return False
    return datetime.now(timezone.utc) - refreshed_at < CACHE_TTL


def get_config_options(agent_name: str) -> list[dict[str, Any]] | None:
    data = _read()
    entry = data.get(agent_name)
    if not entry:
        return None
    return entry.get("config_options")


def get_modes(agent_name: str) -> dict[str, Any] | None:
    data = _read()
    entry = data.get(agent_name)
    if not entry:
        return None
    return entry.get("modes")


def get_commands(agent_name: str) -> list[dict[str, str]] | None:
    data = _read()
    entry = data.get(agent_name)
    if not entry:
        return None
    return entry.get("commands")


def store(
    agent_name: str, config_options: Any, modes: Any, commands: Any = None
) -> None:
    data = _read()

    co_list: list[dict[str, Any]] = []
    if config_options:
        for opt in config_options:
            val = getattr(opt, "current_value", None)
            if val is None:
                val = getattr(opt, "currentValue", None)
            co_list.append(
                {
                    "id": getattr(opt, "id", None),
                    "name": getattr(opt, "name", None),
                    "type": getattr(opt, "type", None),
                    "current_value": val,
                    "options": _serialize_select_options(opt),
                }
            )

    mode_dict: dict[str, Any] | None = None
    if modes:
        current = getattr(modes, "current_mode_id", None) or getattr(
            modes, "currentModeId", None
        )
        available = getattr(modes, "available_modes", None) or getattr(
            modes, "availableModes", None
        )
        available_list: list[dict[str, str | None]] = []
        if available:
            for m in available:
                available_list.append(
                    {
                        "id": getattr(m, "id", None),
                        "name": getattr(m, "name", None),
                    }
                )
        mode_dict = {"current_mode_id": current, "available_modes": available_list}

    cmd_list: list[dict[str, str]] = []
    if commands:
        for cmd in commands:
            name = getattr(cmd, "name", None)
            desc = getattr(cmd, "description", None)
            if name:
                cmd_list.append({"name": name, "description": desc or ""})

    data[agent_name] = {
        "config_options": co_list,
        "modes": mode_dict,
        "commands": cmd_list,
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
    }
    _write(data)


def update_model(agent_name: str, model_id: str) -> None:
    """Update the cached current model value for an agent."""
    data = _read()
    entry = data.get(agent_name)
    if entry:
        config_options = entry.get("config_options", [])
        for opt in config_options:
            if opt.get("id") == "model":
                opt["current_value"] = model_id
                break
        _write(data)


def update_mode(agent_name: str, mode_id: str) -> None:
    """Update the cached current mode for an agent."""
    data = _read()
    entry = data.get(agent_name)
    if entry:
        modes = entry.get("modes")
        if modes:
            modes["current_mode_id"] = mode_id
            _write(data)


def update_commands(agent_name: str, commands: Any) -> None:
    """Update the cached commands list for an agent."""
    data = _read()
    entry = data.get(agent_name)
    if entry:
        cmd_list: list[dict[str, str]] = []
        if commands:
            for cmd in commands:
                name = getattr(cmd, "name", None)
                desc = getattr(cmd, "description", None)
                if name:
                    cmd_list.append({"name": name, "description": desc or ""})
        entry["commands"] = cmd_list
        _write(data)


def _serialize_select_options(opt: Any) -> list[dict[str, str]]:
    options = getattr(opt, "options", None)
    if not options:
        return []
    result: list[dict[str, str]] = []
    for item in options:
        item_id = getattr(item, "id", None)
        item_name = getattr(item, "name", None)
        if item_id:
            result.append({"id": item_id, "name": item_name or item_id})
        else:
            opts = getattr(item, "options", None)
            if opts:
                for sub in opts:
                    sub_id = getattr(sub, "id", None)
                    if sub_id:
                        result.append(
                            {
                                "id": sub_id,
                                "name": getattr(sub, "label", None) or sub_id,
                            }
                        )
    return result
