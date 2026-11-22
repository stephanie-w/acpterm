from __future__ import annotations

from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import CliRunner

from acpterm import session_store
from acpterm.cli import (
    _fetch_and_cache_agent_info,
    app,
    complete_session,
)


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate config and session storage so tests never touch the home directory."""
    monkeypatch.setattr("acpterm.config.CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(
        "acpterm.session_store.SESSIONS_FILE", tmp_path / "sessions.json"
    )
    return tmp_path


def _invoke(args: list[str]) -> Any:
    return CliRunner().invoke(app, args)


def _save_session(agent: str, directory: Path, name: str, session_id: str) -> None:
    session_store.save(agent, str(directory.resolve()), session_id, name)


def test_help_lists_cwd_option(cli_env: Path) -> None:
    result = _invoke(["--help"])

    assert result.exit_code == 0
    assert "-C" in result.output
    plain_output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "--cwd" in plain_output
    assert "Working directory" in plain_output


@pytest.mark.parametrize("bad_target", ["missing", "file"])
def test_invalid_cwd_exits_2(cli_env: Path, tmp_path: Path, bad_target: str) -> None:
    target = tmp_path / "not-a-directory"
    if bad_target == "file":
        target.write_text("data")
    else:
        target = tmp_path / "missing-dir"

    result = _invoke(["-C", str(target), "sessions", "list"])

    assert result.exit_code == 2
    assert "Invalid value" in result.output


def test_sessions_list_scopes_to_absolute_cwd(cli_env: Path, tmp_path: Path) -> None:
    target = tmp_path / "target-repo"
    target.mkdir()
    _save_session("fake-agent", target, "target-session", "sid-target")
    _save_session("fake-agent", tmp_path / "other-repo", "other-session", "sid-other")

    result = _invoke(["-a", "fake-agent", "-C", str(target), "sessions", "list"])

    assert result.exit_code == 0
    assert "target-session" in result.output
    assert "other-session" not in result.output


def test_sessions_list_accepts_relative_cwd(
    cli_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target-repo"
    target.mkdir()
    _save_session("fake-agent", target, "target-session", "sid-target")
    monkeypatch.chdir(tmp_path)

    result = _invoke(["-a", "fake-agent", "-C", "target-repo", "sessions", "list"])

    assert result.exit_code == 0
    assert "target-session" in result.output


def test_sessions_list_defaults_to_shell_cwd(
    cli_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _save_session("fake-agent", tmp_path, "shell-session", "sid-shell")
    monkeypatch.chdir(tmp_path)

    result = _invoke(["-a", "fake-agent", "sessions", "list"])

    assert result.exit_code == 0
    assert "shell-session" in result.output


def test_sessions_show_scopes_to_cwd(cli_env: Path, tmp_path: Path) -> None:
    target = tmp_path / "target-repo"
    target.mkdir()
    _save_session("fake-agent", target, "target-session", "sid-target")

    result = _invoke(
        ["-a", "fake-agent", "-C", str(target), "sessions", "show", "target-session"]
    )

    assert result.exit_code == 0
    assert "target-session" in result.output


class _FakeAgent:
    def __init__(self, **_kwargs: object) -> None:
        pass

    async def start(self, **_kwargs: object) -> None:
        pass

    async def close_session(self) -> None:
        pass

    async def stop(self) -> None:
        pass


def test_sessions_close_scopes_lookup_and_removal(
    cli_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target-repo"
    target.mkdir()
    _save_session("fake-agent", target, "target-session", "sid-target")
    monkeypatch.setattr("acpterm.cli.ACPAgent", _FakeAgent)

    result = _invoke(
        ["-a", "fake-agent", "-C", str(target), "sessions", "close", "target-session"]
    )

    assert result.exit_code == 0
    assert "closed and removed" in result.output
    assert (
        session_store.get_entry("fake-agent", str(target.resolve()), "target-session")
        is None
    )


def test_resume_uses_cwd_for_latest_lookup(
    cli_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target-repo"
    target.mkdir()
    _save_session("fake-agent", target, "target-session", "sid-target")
    run_prompt = AsyncMock()
    monkeypatch.setattr("acpterm.cli._run_prompt", run_prompt)

    result = _invoke(["-a", "fake-agent", "-C", str(target), "-r", "prompt", "hello"])

    assert result.exit_code == 0
    assert run_prompt.await_args is not None
    assert run_prompt.await_args.kwargs["session_name"] == "target-session"


def test_prompt_forwards_project_root_without_changing_process_cwd(
    cli_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target-repo"
    caller = tmp_path / "caller"
    target.mkdir()
    caller.mkdir()
    (caller / "prompt.md").write_text("prompt from caller", encoding="utf-8")
    monkeypatch.chdir(caller)
    run_prompt = AsyncMock()
    monkeypatch.setattr("acpterm.cli._run_prompt", run_prompt)

    result = _invoke(
        ["-a", "fake-agent", "-C", str(target), "prompt", "--file", "prompt.md"]
    )

    assert result.exit_code == 0
    assert run_prompt.await_args is not None
    kwargs = run_prompt.await_args.kwargs
    assert kwargs["project_root"] == target.resolve()
    assert kwargs["prompt_text"] == "prompt from caller"
    assert Path.cwd() == caller


def test_models_set_forwards_cwd(
    cli_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target-repo"
    target.mkdir()
    set_model = AsyncMock(return_value=True)
    monkeypatch.setattr("acpterm.cli._set_model_on_agent", set_model)

    result = _invoke(
        ["-a", "fake-agent", "-C", str(target), "models", "set", "gpt-test"]
    )

    assert result.exit_code == 0
    set_model.assert_awaited_once_with(
        "fake-agent", str(target.resolve()), "default", "gpt-test", False
    )


def test_modes_set_forwards_cwd(
    cli_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target-repo"
    target.mkdir()
    set_mode = AsyncMock(return_value=True)
    monkeypatch.setattr("acpterm.cli._set_mode_on_agent", set_mode)

    result = _invoke(["-a", "fake-agent", "-C", str(target), "modes", "set", "code"])

    assert result.exit_code == 0
    set_mode.assert_awaited_once_with(
        "fake-agent", str(target.resolve()), "default", "code", False
    )


@pytest.mark.parametrize(
    ("command", "cache_getter", "cached_value"),
    [
        (["models", "list"], "get_config_options", []),
        (["modes", "list"], "get_modes", None),
        (["commands", "list"], "get_commands", []),
    ],
)
def test_cache_refresh_forwards_cwd(
    cli_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: list[str],
    cache_getter: str,
    cached_value: object,
) -> None:
    target = tmp_path / "target-repo"
    target.mkdir()
    fetch = AsyncMock()
    monkeypatch.setattr("acpterm.cli.agent_cache.is_fresh", lambda _agent: False)
    monkeypatch.setattr("acpterm.cli._fetch_and_cache_agent_info", fetch)
    monkeypatch.setattr(
        f"acpterm.cli.agent_cache.{cache_getter}", lambda _agent: cached_value
    )

    result = _invoke(["-a", "fake-agent", "-C", str(target), *command])

    assert result.exit_code == 0
    assert fetch.await_args is not None
    assert fetch.await_args.kwargs["cwd"] == target.resolve()


class _FakeCachingConnection:
    def __init__(self) -> None:
        self.initialize = AsyncMock()
        self.new_session = AsyncMock(
            return_value=SimpleNamespace(session_id=None, config_options=[], modes=None)
        )
        self.close_session = AsyncMock()
        self.close = AsyncMock()


class _FakeTransport:
    async def __aenter__(self) -> tuple[object, object, None]:
        return object(), object(), None

    async def __aexit__(self, *_exc: object) -> bool:
        return False


@pytest.mark.asyncio
async def test_fetch_and_cache_forwards_cwd_to_transport_and_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target-repo"
    target.mkdir()
    conn = _FakeCachingConnection()
    spawn = MagicMock(return_value=_FakeTransport())
    monkeypatch.setattr(
        "acp.client.connection.ClientSideConnection", lambda *_a, **_k: conn
    )
    monkeypatch.setattr("acp.transports.spawn_stdio_transport", spawn)
    monkeypatch.setattr(
        "acpterm.config.resolve_agent_command", lambda _name: ["fake-agent"]
    )
    monkeypatch.setattr("acpterm.cli.agent_cache.store", lambda *_a, **_k: None)
    monkeypatch.setattr("acpterm.cli.asyncio.sleep", AsyncMock())

    await _fetch_and_cache_agent_info("fake-agent", cwd=target)

    assert spawn.call_args.kwargs["cwd"] == str(target.resolve())
    assert conn.new_session.await_args is not None
    assert conn.new_session.await_args.kwargs["cwd"] == str(target.resolve())


def _context(params: dict[str, Any], parent: Any = None) -> Any:
    return cast("Any", SimpleNamespace(params=params, parent=parent))


def test_complete_session_uses_own_cwd_param(cli_env: Path, tmp_path: Path) -> None:
    target = tmp_path / "target-repo"
    target.mkdir()
    _save_session("fake-agent", target, "target-session", "sid-target")

    names = complete_session(_context({"cwd": target, "agent": "fake-agent"}), "tar")

    assert names == ["target-session"]


def test_complete_session_uses_parent_cwd_param(cli_env: Path, tmp_path: Path) -> None:
    target = tmp_path / "target-repo"
    target.mkdir()
    _save_session("fake-agent", target, "target-session", "sid-target")
    parent = _context({"cwd": target, "agent": "fake-agent"})

    names = complete_session(_context({"agent": "fake-agent"}, parent=parent), "tar")

    assert names == ["target-session"]


def test_complete_session_falls_back_to_shell_cwd(
    cli_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _save_session("fake-agent", tmp_path, "shell-session", "sid-shell")

    names = complete_session(_context({"cwd": None}), "she")

    assert names == ["shell-session"]


def test_complete_session_falls_back_for_invalid_cwd(
    cli_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _save_session("fake-agent", tmp_path, "shell-session", "sid-shell")

    names = complete_session(_context({"cwd": tmp_path / "missing"}), "she")

    assert names == ["shell-session"]


def test_readme_documents_cwd_option(cli_env: Path) -> None:
    readme = Path(__file__).parent.parent / "README.md"

    assert "`-C, --cwd <path>`" in readme.read_text(encoding="utf-8")
