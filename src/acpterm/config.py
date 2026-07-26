from __future__ import annotations

import contextlib
from pathlib import Path
import shlex
import shutil

from pydantic import BaseModel, Field

from .hooks import HookDefinition


CONFIG_FILE = Path.home() / ".acpterm" / "config.json"


class Config(BaseModel):
    """Configuration schema for acpterm."""

    agents: dict[str, str] = Field(default_factory=dict)
    agent_models: dict[str, list[dict[str, str]]] = Field(default_factory=dict)
    default_models: dict[str, str] = Field(default_factory=dict)
    default_modes: dict[str, str] = Field(default_factory=dict)
    max_prompt_chars: int = Field(default=100000)
    hooks: list[HookDefinition] = Field(default_factory=list)

    def get_agent_command(self, agent_name: str) -> list[str]:
        """Validate and resolve the full spawn command for an agent.

        Looks up ``agent_name`` in ``self.agents``.
        If found, returns the configured command split into args.
        Falls back to the bare agent binary name if found on PATH.

        Raises:
            ValueError: If agent_name is not registered in config.json and not found on PATH.
        """
        if agent_name in self.agents:
            command_str = self.agents[agent_name]
        else:
            if not shutil.which(agent_name):
                configured = ", ".join(f"'{a}'" for a in self.agents.keys()) or "none"
                msg = (
                    f"Agent '{agent_name}' is not configured in {CONFIG_FILE} under 'agents' "
                    f"and was not found on your system PATH.\n\n"
                    f"Configured agents in config: {configured}\n\n"
                    f"To configure '{agent_name}', add it to {CONFIG_FILE}:\n"
                    f'  "agents": {{\n    "{agent_name}": "path/to/{agent_name} acp"\n  }}'
                )
                raise ValueError(msg)
            command_str = agent_name
        return shlex.split(command_str)

    def get_default_model(self, agent_name: str) -> str | None:
        """Get the configured default model for the agent."""
        return self.default_models.get(agent_name)

    def set_default_model(self, agent_name: str, model_id: str) -> None:
        """Set the default model for the agent and save the config."""
        self.default_models[agent_name] = model_id
        self.save()

    def get_default_mode(self, agent_name: str) -> str | None:
        """Get the configured default mode for the agent."""
        return self.default_modes.get(agent_name)

    def set_default_mode(self, agent_name: str, mode_id: str) -> None:
        """Set the default mode for the agent and save the config."""
        self.default_modes[agent_name] = mode_id
        self.save()

    @classmethod
    def load(cls) -> Config:
        """Load and validate the configuration from disk.

        Returns:
            Config: The validated configuration object, falling back to defaults
                on failure or if the file does not exist.
        """
        if CONFIG_FILE.exists():
            try:
                return cls.model_validate_json(CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception as e:
                import sys

                print(
                    f"Warning: Failed to parse configuration file {CONFIG_FILE}: {e}\n"
                    f"Falling back to default configuration.",
                    file=sys.stderr,
                )
        return cls()

    def save(self) -> None:
        """Save the configuration to disk."""
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(self.model_dump_json(indent=2), encoding="utf-8")

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
    """Resolve the full spawn command for an agent using the loaded Config model."""
    return Config.load().get_agent_command(agent_name)
