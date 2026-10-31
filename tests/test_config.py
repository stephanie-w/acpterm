from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from acpterm.cli import _pick_agent, _resolve_agent, app
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


def _write_config(path: Path, default_agent: str | None) -> None:
    path.write_text(
        Config(default_agent=default_agent).model_dump_json(), encoding="utf-8"
    )


def test_resolve_agent_prefers_flag_over_config_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(tmp_path / "config.json", "kiro")
    monkeypatch.setattr("acpterm.config.CONFIG_FILE", tmp_path / "config.json")

    assert _resolve_agent("opencode") == "opencode"


def test_resolve_agent_uses_config_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(tmp_path / "config.json", "kiro")
    monkeypatch.setattr("acpterm.config.CONFIG_FILE", tmp_path / "config.json")

    assert _resolve_agent(None) == "kiro"


def test_resolve_agent_returns_none_without_flag_or_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("acpterm.config.CONFIG_FILE", tmp_path / "missing.json")

    assert _resolve_agent(None) is None


def test_callback_exits_2_when_no_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("acpterm.config.CONFIG_FILE", tmp_path / "missing.json")
    runner = CliRunner()

    result = runner.invoke(app, ["prompt", "hello"])

    assert result.exit_code == 2
    assert "no agent specified" in result.output


def test_callback_allows_config_subcommand_without_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("acpterm.config.CONFIG_FILE", tmp_path / "config.json")
    runner = CliRunner()

    show_result = runner.invoke(app, ["config", "show"])
    init_result = runner.invoke(app, ["config", "init"])

    assert show_result.exit_code == 0
    assert init_result.exit_code == 0
