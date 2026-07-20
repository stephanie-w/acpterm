from __future__ import annotations

import shlex
from pathlib import Path
from pydantic import BaseModel, Field

CONFIG_FILE = Path.home() / ".acpterm" / "config.json"


class Config(BaseModel):
    """Configuration schema for acpterm."""

    agents: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def load(cls) -> Config:
        """Load and validate the configuration from disk.

        Returns:
            Config: The validated configuration object, falling back to defaults
                on failure or if the file does not exist.
        """
        if CONFIG_FILE.exists():
            try:
                return cls.model_validate_json(CONFIG_FILE.read_text())
            except Exception:
                pass
        return cls()


def resolve_agent_command(agent_name: str) -> list[str]:
    """Resolve the full spawn command for an agent.

    Looks up ``agent_name`` in ``~/.acpterm/config.json`` under the ``agents`` key.
    If found, returns the configured command split into args.
    Falls back to the bare agent binary name.
    """
    config = Config.load()
    command_str = config.agents.get(agent_name, agent_name)
    return shlex.split(command_str)
