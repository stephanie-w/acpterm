from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timedelta, UTC
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

from acp import schema as acp_schema


CACHE_FILE = Path.home() / ".acpterm" / "agent_cache.json"
CACHE_TTL = timedelta(days=7)


def _read() -> dict[str, Any]:
    if not CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        print(
            f"Warning: Failed to read agent cache {CACHE_FILE}: {e}. Ignoring it.",
            file=sys.stderr,
        )
        return {}
    if not isinstance(data, dict):
        print(
            f"Warning: Agent cache {CACHE_FILE} does not contain a JSON object."
            " Ignoring it.",
            file=sys.stderr,
        )
        return {}
    return data


def _write(data: dict[str, Any]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=CACHE_FILE.parent,
            delete=False,
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)
            json.dump(data, tmp_file, indent=2)
        os.replace(tmp_path, CACHE_FILE)
    except BaseException:
        if tmp_path is not None:
            with suppress(OSError):
                tmp_path.unlink()
        raise


def is_fresh(agent_name: str) -> bool:
    data = _read()
    entry = data.get(agent_name)
    if not isinstance(entry, dict):
        return False
    refreshed_at = entry.get("refreshed_at")
    if not isinstance(refreshed_at, str):
        return False
    try:
        refreshed = datetime.fromisoformat(refreshed_at)
    except ValueError:
        return False
    return datetime.now(UTC) - refreshed < CACHE_TTL


def get_config_options(agent_name: str) -> list[dict[str, Any]] | None:
    entry = _get_entry(agent_name)
    if not entry:
        return None
    return entry.get("config_options")


def get_modes(agent_name: str) -> dict[str, Any] | None:
    entry = _get_entry(agent_name)
    if not entry:
        return None
    return entry.get("modes")


def get_commands(agent_name: str) -> list[dict[str, str]] | None:
    entry = _get_entry(agent_name)
    if not entry:
        return None
    return entry.get("commands")


def _get_entry(agent_name: str) -> dict[str, Any] | None:
    entry = _read().get(agent_name)
    return entry if isinstance(entry, dict) else None


ConfigOption = (
    acp_schema.SessionConfigOptionSelect | acp_schema.SessionConfigOptionBoolean
)


def store(
    agent_name: str,
    config_options: list[ConfigOption] | None = None,
    modes: acp_schema.SessionModeState | None = None,
    commands: list[acp_schema.AvailableCommand] | None = None,
) -> None:
    data = _read()
    existing = data.get(agent_name)
    if not isinstance(existing, dict):
        existing = {}

    co_list: list[dict[str, Any]] = existing.get("config_options", [])
    if config_options is not None:
        co_list = []
        for opt in config_options:
            co_list.append(
                {
                    "id": opt.id,
                    "name": opt.name,
                    "type": opt.type,
                    "current_value": opt.current_value,
                    "options": _serialize_select_options(opt),
                }
            )

    mode_dict: dict[str, Any] | None = existing.get("modes")
    if modes is not None:
        available_list: list[dict[str, str | None]] = []
        if modes.available_modes:
            for m in modes.available_modes:
                available_list.append(
                    {
                        "id": m.id,
                        "name": m.name,
                        "description": m.description,
                    }
                )
        mode_dict = {
            "current_mode_id": modes.current_mode_id,
            "available_modes": available_list,
        }

    cmd_list: list[dict[str, str]] = existing.get("commands", [])
    if commands is not None:
        cmd_list = []
        for cmd in commands:
            if cmd.name:
                cmd_list.append(
                    {"name": cmd.name, "description": cmd.description or ""}
                )

    data[agent_name] = {
        "config_options": co_list,
        "modes": mode_dict,
        "commands": cmd_list,
        "refreshed_at": datetime.now(UTC).isoformat(),
    }
    _write(data)


def update_model(agent_name: str, model_id: str) -> None:
    """Update the cached current model value for an agent."""
    data = _read()
    entry = _get_entry(agent_name)
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
    entry = _get_entry(agent_name)
    if entry:
        modes = entry.get("modes")
        if modes:
            modes["current_mode_id"] = mode_id
            _write(data)


def update_commands(
    agent_name: str, commands: list[acp_schema.AvailableCommand] | None
) -> None:
    """Update the cached commands list for an agent, creating an entry if needed."""
    data = _read()
    entry = data.get(agent_name)
    if not isinstance(entry, dict):
        entry = {}
        data[agent_name] = entry
    cmd_list: list[dict[str, str]] = []
    if commands:
        for cmd in commands:
            if cmd.name:
                cmd_list.append(
                    {"name": cmd.name, "description": cmd.description or ""}
                )
    entry["commands"] = cmd_list
    _write(data)


def _serialize_select_options(opt: ConfigOption) -> list[dict[str, str]]:
    if not isinstance(opt, acp_schema.SessionConfigOptionSelect):
        return []
    result: list[dict[str, str]] = []
    for item in opt.options:
        if isinstance(item, acp_schema.SessionConfigSelectOption):
            if item.value:
                result.append({"id": item.value, "name": item.name or item.value})
        elif isinstance(item, acp_schema.SessionConfigSelectGroup):
            for sub in item.options:
                if sub.value:
                    result.append({"id": sub.value, "name": sub.name or sub.value})
    return result
