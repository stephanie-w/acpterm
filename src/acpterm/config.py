from __future__ import annotations

import shlex
from pathlib import Path
from pydantic import BaseModel, Field

CONFIG_FILE = Path.home() / ".acpterm" / "config.json"


class Config(BaseModel):
    """Configuration schema for acpterm."""

    agents: dict[str, str] = Field(default_factory=dict)
    agent_models: dict[str, list[dict[str, str]]] = Field(default_factory=dict)

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

    def get_agent_models(self, agent_name: str) -> list[dict[str, str]]:
        """Get the configured models list for the agent, with defaults as fallback."""
        if agent_name in self.agent_models:
            return self.agent_models[agent_name]

        # Default fallbacks for common agents
        defaults = {
            "opencode": [
                {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro"},
                {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash"},
                {"id": "claude-3-5-sonnet", "name": "Claude 3.5 Sonnet"},
                {"id": "gpt-4o", "name": "GPT 4o"},
            ],
            "kiro": [
                {"id": "kiro-large", "name": "Kiro Large"},
                {"id": "kiro-medium", "name": "Kiro Medium"},
            ],
        }
        return defaults.get(agent_name, [])


def resolve_agent_command(agent_name: str) -> list[str]:
    """Resolve the full spawn command for an agent.

    Looks up ``agent_name`` in ``~/.acpterm/config.json`` under the ``agents`` key.
    If found, returns the configured command split into args.
    Falls back to the bare agent binary name.
    """
    config = Config.load()
    command_str = config.agents.get(agent_name, agent_name)
    return shlex.split(command_str)
