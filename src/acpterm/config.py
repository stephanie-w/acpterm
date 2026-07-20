from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

CONFIG_FILE = Path.home() / ".acpterm" / "config.json"


def _read() -> dict[str, Any]:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


def resolve_agent_command(agent_name: str) -> list[str]:
    """Resolve the full spawn command for an agent.

    Looks up ``agent_name`` in ``~/.acpterm/config.json`` under the ``agents`` key.
    If found, returns the configured command split into args.
    Falls back to the bare agent binary name.
    """
    config = _read()
    agents = config.get("agents", {})
    command_str = agents.get(agent_name, agent_name)
    return shlex.split(command_str)
