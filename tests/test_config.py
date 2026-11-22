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


def test_prompt_exits_2_when_no_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("acpterm.config.CONFIG_FILE", tmp_path / "missing.json")
    runner = CliRunner()

    result = runner.invoke(app, ["prompt", "hello"])

    assert result.exit_code == 2
    assert "no agent specified" in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["prompt", "--help"],
        ["models", "list", "--help"],
        ["modes", "list", "--help"],
        ["sessions", "list", "--help"],
    ],
)
def test_subcommand_help_exits_0_without_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    monkeypatch.setattr("acpterm.config.CONFIG_FILE", tmp_path / "missing.json")
    runner = CliRunner()

    result = runner.invoke(app, args)

    assert result.exit_code == 0


def test_callback_allows_config_subcommand_without_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("acpterm.config.CONFIG_FILE", tmp_path / "config.json")
    runner = CliRunner()

    show_result = runner.invoke(app, ["config", "show"])
    init_result = runner.invoke(app, ["config", "init"])

    assert show_result.exit_code == 0
    assert init_result.exit_code == 0


def test_config_init_writes_default_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "config.json"
    monkeypatch.setattr("acpterm.config.CONFIG_FILE", config_file)
    runner = CliRunner()

    result = runner.invoke(app, ["config", "init"])

    assert result.exit_code == 0
    assert (
        Config.model_validate_json(config_file.read_text()).default_agent == "opencode"
    )


@pytest.mark.parametrize(
    "corrupt_content",
    [
        "{not valid json",
        '{"agents": {"opencode": "opencode"}, "max_prompt_chars": "not-an-int"}',
    ],
)
def test_corrupt_config_refuses_save_and_preserves_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corrupt_content: str
) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(corrupt_content, encoding="utf-8")
    monkeypatch.setattr("acpterm.config.CONFIG_FILE", config_file)

    config = Config.load()

    with pytest.raises(RuntimeError, match="Refusing to overwrite"):
        config.save()
    with pytest.raises(RuntimeError, match="Refusing to overwrite"):
        config.set_default_model("opencode", "gpt-4o")
    with pytest.raises(RuntimeError, match="Refusing to overwrite"):
        config.set_default_mode("opencode", "code")

    assert config_file.read_text(encoding="utf-8") == corrupt_content
    assert config.default_models == {}
    assert config.default_modes == {}


def test_missing_config_can_save_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "config.json"
    monkeypatch.setattr("acpterm.config.CONFIG_FILE", config_file)

    config = Config.load()
    config.set_default_model("opencode", "gpt-4o")
    config.set_default_mode("opencode", "code")

    reloaded = Config.load()
    assert reloaded.get_default_model("opencode") == "gpt-4o"
    assert reloaded.get_default_mode("opencode") == "code"


@pytest.mark.parametrize(
    ("subcommand", "helper_attr", "value"),
    [
        ("models", "_set_model_on_agent", "gpt-4o"),
        ("modes", "_set_mode_on_agent", "code"),
    ],
)
def test_cli_set_preserves_corrupt_config_when_no_active_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    subcommand: str,
    helper_attr: str,
    value: str,
) -> None:
    corrupt_content = '{"agents": {"opencode": "opencode"}, broken}'
    config_file = tmp_path / "config.json"
    config_file.write_text(corrupt_content, encoding="utf-8")
    monkeypatch.setattr("acpterm.config.CONFIG_FILE", config_file)

    async def fake_set(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(f"acpterm.cli.{helper_attr}", fake_set)
    runner = CliRunner()

    result = runner.invoke(app, ["-a", "opencode", subcommand, "set", value])

    assert result.exit_code == 1
    assert "Refusing to overwrite" in result.output
    assert "Saved" not in result.output
    assert config_file.read_text(encoding="utf-8") == corrupt_content


@pytest.mark.parametrize(
    ("subcommand", "helper_attr", "value"),
    [
        ("models", "_set_model_on_agent", "gpt-4o"),
        ("modes", "_set_mode_on_agent", "code"),
    ],
)
def test_cli_set_preserves_corrupt_config_when_session_updated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    subcommand: str,
    helper_attr: str,
    value: str,
) -> None:
    corrupt_content = '{"agents": {"opencode": "opencode"}, broken}'
    config_file = tmp_path / "config.json"
    config_file.write_text(corrupt_content, encoding="utf-8")
    monkeypatch.setattr("acpterm.config.CONFIG_FILE", config_file)

    async def fake_set(*args: object, **kwargs: object) -> bool:
        return True

    monkeypatch.setattr(f"acpterm.cli.{helper_attr}", fake_set)
    runner = CliRunner()

    result = runner.invoke(app, ["-a", "opencode", subcommand, "set", value])

    assert result.exit_code == 0
    assert "Refusing to overwrite" in result.output
    assert "default not saved" in result.output
    assert config_file.read_text(encoding="utf-8") == corrupt_content
