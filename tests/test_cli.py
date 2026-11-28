from __future__ import annotations

import json
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from acp import schema as acp_schema
import pytest
import typer
from typer.testing import CliRunner

from acpterm import agent_cache, session_store
from acpterm.cli import (
    _fetch_and_cache_agent_info,
    _resolve_prompt_text,
    _run_prompt,
    _set_mode_on_agent,
    _set_model_on_agent,
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


class _FakePromptAgent:
    """Minimal agent double whose prompt turn can fail on demand."""

    prompt_exception: BaseException | None = None
    session_id_after_start: str | None = "sid-new"

    def __init__(self, **_kwargs: object) -> None:
        self.session_id: str | None = None
        self.cancelled = False
        self.stopped = False

    async def start(self, **_kwargs: object) -> None:
        self.session_id = self.session_id_after_start

    async def send_prompt(self, _prompt_text: str, **_kwargs: object) -> None:
        if self.prompt_exception is not None:
            raise self.prompt_exception

    async def cancel(self) -> None:
        self.cancelled = True

    async def stop(self) -> None:
        self.stopped = True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc_type", "expected_exit"),
    [(KeyboardInterrupt, 130), (RuntimeError, None)],
    ids=["interrupted", "failed_prompt"],
)
async def test_prompt_failure_still_persists_session(
    cli_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exc_type: type[BaseException],
    expected_exit: int | None,
) -> None:
    target = tmp_path / "target-repo"
    target.mkdir()
    monkeypatch.setattr("acpterm.cli.ACPAgent", _FakePromptAgent)
    monkeypatch.setattr(_FakePromptAgent, "prompt_exception", exc_type("boom"))
    monkeypatch.setattr("acpterm.cli.asyncio.sleep", AsyncMock())

    call = _run_prompt(
        "fake-agent",
        "hello",
        persist=True,
        project_root=target,
    )

    if expected_exit is None:
        with pytest.raises(RuntimeError):
            await call
    else:
        with pytest.raises(typer.Exit) as exit_info:
            await call
        assert exit_info.value.exit_code == expected_exit

    assert (
        session_store.get("fake-agent", str(target.absolute()), "default") == "sid-new"
    )


class _FakeSetModelAgent:
    def __init__(self, **_kwargs: object) -> None:
        self.session_id: str | None = None

    async def start(self, **_kwargs: object) -> None:
        self.session_id = "sid-fallback"

    async def set_model(self, _model_id: str) -> None:
        pass

    async def stop(self) -> None:
        pass


class _FakeSetModeAgent(_FakeSetModelAgent):
    async def set_mode(self, _mode_id: str) -> str | None:
        return _mode_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setter", "agent_double"),
    [
        (_set_model_on_agent, _FakeSetModelAgent),
        (_set_mode_on_agent, _FakeSetModeAgent),
    ],
    ids=["set_model", "set_mode"],
)
async def test_set_on_agent_persists_stale_id_fallback(
    cli_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    setter: Any,
    agent_double: type[Any],
) -> None:
    target = tmp_path / "target-repo"
    target.mkdir()
    cwd = str(target.resolve())
    _save_session("fake-agent", target, "work", "sid-stale")
    monkeypatch.setattr("acpterm.cli.ACPAgent", agent_double)

    assert await setter("fake-agent", cwd, "work", "value") is True

    assert session_store.get("fake-agent", cwd, "work") == "sid-fallback"


@pytest.mark.parametrize(
    ("command", "setter_name"),
    [
        (["models", "set", "gpt-test"], "_set_model_on_agent"),
        (["modes", "set", "code"], "_set_mode_on_agent"),
    ],
    ids=["set_model", "set_mode"],
)
def test_rejected_default_not_persisted(
    cli_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: list[str],
    setter_name: str,
) -> None:
    config_file = cli_env / "config.json"
    config_file.write_text(
        json.dumps({"agents": {"fake-agent": "fake-agent acp"}}), encoding="utf-8"
    )
    before = config_file.read_text(encoding="utf-8")
    monkeypatch.setattr(f"acpterm.cli.{setter_name}", AsyncMock(return_value=False))

    result = _invoke(["-a", "fake-agent", "-C", str(cli_env), *command])

    assert result.exit_code == 1
    assert config_file.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_fetch_and_cache_preserves_commands_without_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("acpterm.agent_cache.CACHE_FILE", tmp_path / "agent_cache.json")
    target = tmp_path / "target-repo"
    target.mkdir()
    agent_cache.store(
        "fake-agent",
        commands=[acp_schema.AvailableCommand(name="compact", description="Summarize")],
    )
    conn = _FakeCachingConnection()
    monkeypatch.setattr(
        "acp.client.connection.ClientSideConnection", lambda *_a, **_k: conn
    )
    monkeypatch.setattr(
        "acp.transports.spawn_stdio_transport", MagicMock(return_value=_FakeTransport())
    )
    monkeypatch.setattr(
        "acpterm.config.resolve_agent_command", lambda _name: ["fake-agent"]
    )
    monkeypatch.setattr("acpterm.cli.asyncio.sleep", AsyncMock())

    await _fetch_and_cache_agent_info("fake-agent", cwd=target)

    assert agent_cache.get_commands("fake-agent") == [
        {"name": "compact", "description": "Summarize"}
    ]


@pytest.mark.parametrize(
    ("command", "error"),
    [
        (["models", "list"], ValueError("no command configured")),
        (["modes", "list"], ValueError("no command configured")),
        (["commands", "list"], ValueError("no command configured")),
        (["models", "list"], FileNotFoundError("agent binary missing")),
        (["modes", "list"], FileNotFoundError("agent binary missing")),
        (["commands", "list"], FileNotFoundError("agent binary missing")),
    ],
    ids=[
        "models_value",
        "modes_value",
        "commands_value",
        "models_missing",
        "modes_missing",
        "commands_missing",
    ],
)
def test_resolution_error_exits_1(
    cli_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: list[str],
    error: Exception,
) -> None:
    target = tmp_path / "target-repo"
    target.mkdir()
    monkeypatch.setattr("acpterm.cli.agent_cache.is_fresh", lambda _agent: False)
    monkeypatch.setattr(
        "acpterm.cli._fetch_and_cache_agent_info", AsyncMock(side_effect=error)
    )

    result = _invoke(["-a", "fake-agent", "-C", str(target), *command])

    assert result.exit_code == 1
    assert str(error) in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("stdin_text", ["piped line\n", ""], ids=["piped", "empty"])
def test_stdin_combined_with_positional_prompt(
    monkeypatch: pytest.MonkeyPatch, stdin_text: str
) -> None:
    monkeypatch.setattr(
        "sys.stdin", SimpleNamespace(read=lambda: stdin_text, isatty=lambda: False)
    )

    resolved = _resolve_prompt_text(["Review this function"], None)

    expected = (
        "piped line\nReview this function" if stdin_text else "Review this function"
    )
    assert resolved == expected


def test_prompt_file_directory_exits_2(
    cli_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target-repo"
    target.mkdir()
    run_prompt = AsyncMock()
    monkeypatch.setattr("acpterm.cli._run_prompt", run_prompt)

    result = _invoke(
        ["-a", "fake-agent", "-C", str(target), "prompt", "--file", str(tmp_path)]
    )

    assert result.exit_code == 2
    assert "directory" in result.output.lower()
    run_prompt.assert_not_awaited()


def test_close_missing_session_exits_1(cli_env: Path, tmp_path: Path) -> None:
    target = tmp_path / "target-repo"
    target.mkdir()

    result = _invoke(
        [
            "-a",
            "fake-agent",
            "-C",
            str(target),
            "sessions",
            "close",
            "nonexistent",
        ]
    )

    assert result.exit_code == 1
    assert "not found" in result.output


@pytest.mark.asyncio
async def test_run_prompt_passes_actual_stop_reason_to_hooks_without_export(
    cli_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acpterm.hooks import HookDefinition, HookEvent

    captured: list[HookEvent] = []

    class _FakeAgent:
        def __init__(self, **_kwargs: Any) -> None:
            self.session_id = "sid-1"

        async def start(self, **_kwargs: Any) -> None:
            pass

        async def send_prompt(self, _prompt: str, **_kwargs: Any) -> str:
            return "max_tokens"

        async def stop(self) -> None:
            pass

    async def _fake_run_hooks(
        event: HookEvent, _hooks: list[HookDefinition], verbose: bool = False
    ) -> list[str]:
        captured.append(event)
        return []

    monkeypatch.setattr("acpterm.cli.ACPAgent", _FakeAgent)
    monkeypatch.setattr("acpterm.hooks.run_hooks", _fake_run_hooks)

    await _run_prompt(
        agent_binary="fake-agent",
        prompt_text="hello",
        session_name="default",
        persist=False,
        project_root=tmp_path,
        on_turn_end="echo ok",
    )

    assert len(captured) == 1
    assert captured[0].stop_reason == "max_tokens"


@pytest.mark.asyncio
async def test_run_prompt_export_includes_chained_prompt_turns(
    cli_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acpterm.hooks import HookDefinition, HookEvent

    export_path = tmp_path / "transcript.md"
    seen_in_hook: list[str] = []

    class _FakeAgent:
        def __init__(self, **kwargs: Any) -> None:
            self.session_id = "sid-1"
            self._recorder = kwargs.get("transcript_recorder")

        async def start(self, **_kwargs: Any) -> None:
            pass

        async def send_prompt(self, prompt: str, **_kwargs: Any) -> str:
            if self._recorder is not None:
                self._recorder.add_message(f"Reply to: {prompt}\n")
                self._recorder.set_stop_reason("end_turn")
            return "end_turn"

        async def stop(self) -> None:
            pass

    async def _fake_run_hooks(
        event: HookEvent, _hooks: list[HookDefinition], verbose: bool = False
    ) -> list[str]:
        assert event.transcript_path == export_path
        seen_in_hook.append(export_path.read_text(encoding="utf-8"))
        return ["follow up"]

    monkeypatch.setattr("acpterm.cli.ACPAgent", _FakeAgent)
    monkeypatch.setattr("acpterm.hooks.run_hooks", _fake_run_hooks)

    await _run_prompt(
        agent_binary="fake-agent",
        prompt_text="first prompt",
        session_name="default",
        persist=False,
        project_root=tmp_path,
        export=export_path,
        chain_prompt="follow up",
    )

    assert len(seen_in_hook) == 1
    assert "Reply to: first prompt" in seen_in_hook[0]

    final_markdown = export_path.read_text(encoding="utf-8")
    assert "Reply to: first prompt" in final_markdown
    assert "Reply to: follow up" in final_markdown
