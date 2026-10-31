from __future__ import annotations

from acpterm.cli import _pick_agent
from acpterm.config import Config


def test_default_agent_field_defaults_to_none() -> None:
    assert Config().default_agent is None


def test_default_agent_parsed_from_json() -> None:
    config = Config.model_validate_json('{"default_agent": "kiro"}')
    assert config.default_agent == "kiro"


def test_pick_agent_prefers_flag() -> None:
    assert _pick_agent("opencode", "kiro") == "opencode"


def test_pick_agent_uses_config_default() -> None:
    assert _pick_agent(None, "kiro") == "kiro"


def test_pick_agent_returns_none_when_unspecified() -> None:
    assert _pick_agent(None, None) is None
