from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from acp import schema as acp_schema
from acp.exceptions import RequestError
import pytest

from acpterm.acp_agent import (
    _has_model_config_option,
    _is_method_not_found,
    _supports_load_session,
    _supports_resume,
    ACPAgent,
    AgentClient,
)


class _SpawnShortCircuit(RuntimeError):
    """Raised to short-circuit start() after spawn_stdio_transport is invoked."""


def _make_tool_call(kind: str, title: str = "Test tool") -> MagicMock:
    tc = MagicMock()
    tc.title = title
    kind_mock = MagicMock()
    kind_mock.value = kind
    tc.kind = kind_mock
    tc.path = None
    tc.uri = None
    return tc


def _make_option(option_id: str = "allow_always") -> MagicMock:
    opt = MagicMock()
    opt.option_id = option_id
    return opt


@pytest.mark.asyncio
async def test_write_text_file_creates_parent_directories(tmp_path: Path) -> None:
    client = AgentClient(project_root=tmp_path)
    path = "sub/dir/file.txt"
    content = "hello\nworld"

    await client.write_text_file(session_id="session", path=path, content=content)

    written = tmp_path / path
    assert written.read_text() == content


@pytest.mark.asyncio
async def test_read_only_auto_approves_read_kind(tmp_path: Path) -> None:
    """In read_only mode, permission requests with kind='read' are auto-approved."""
    client = AgentClient(read_only=True, silent=True, project_root=tmp_path)
    tool_call = _make_tool_call("read", title="Read File src/foo.py")
    options = [_make_option("allow")]

    resp = await client.request_permission(
        session_id="s1", tool_call=tool_call, options=options
    )

    assert resp.outcome.outcome == "selected"
    assert resp.outcome.option_id == "allow"


@pytest.mark.asyncio
async def test_read_only_blocks_write_kind(tmp_path: Path) -> None:
    """In read_only mode, write_text_file raises RuntimeError."""
    client = AgentClient(read_only=True, silent=True, project_root=tmp_path)

    with pytest.raises(RuntimeError, match="read-only mode"):
        await client.write_text_file(session_id="s1", path="src/foo.py", content="oops")


@pytest.mark.asyncio
async def test_read_only_does_not_auto_approve_non_read_kinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In read_only mode, non-read kinds (e.g. 'edit') fall through to interactive prompt."""
    client = AgentClient(read_only=True, silent=False, project_root=tmp_path)
    tool_call = _make_tool_call("edit", title="Edit File src/foo.py")
    options = [_make_option("allow")]

    # Patch Confirm.ask to simulate the user denying the request
    monkeypatch.setattr("acpterm.acp_agent.Confirm.ask", lambda *_a, **_kw: False)

    resp = await client.request_permission(
        session_id="s1", tool_call=tool_call, options=options
    )

    assert resp.outcome.outcome == "cancelled"


@pytest.mark.asyncio
async def test_start_passes_project_root_as_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, str] = {}

    def fake_spawn(*args: object, **kwargs: object) -> None:
        captured["cwd"] = str(kwargs["cwd"])
        raise _SpawnShortCircuit

    monkeypatch.setattr("acpterm.acp_agent.spawn_stdio_transport", fake_spawn)
    monkeypatch.setattr(
        "acpterm.acp_agent.resolve_agent_command", lambda _name: ["fake-agent"]
    )

    agent = ACPAgent(project_root=tmp_path)

    with pytest.raises(_SpawnShortCircuit):
        await agent.start()

    assert captured["cwd"] == str(tmp_path.absolute())


class _FakeTransport:
    async def __aenter__(self) -> tuple[object, object, None]:
        return object(), object(), None

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _FakeConnection:
    def __init__(self) -> None:
        self.initialize = AsyncMock()
        self.resume_session = AsyncMock()
        self.load_session = AsyncMock()
        self.new_session = AsyncMock()
        self.set_config_option = AsyncMock()
        self._conn = SimpleNamespace(send_request=AsyncMock())


class _NoConfig:
    def get_default_model(self, _binary: str | None = None) -> None:
        return None

    def get_default_mode(self, _binary: str | None = None) -> None:
        return None


def _install_start_harness(
    monkeypatch: pytest.MonkeyPatch, conn: _FakeConnection
) -> None:
    monkeypatch.setattr(
        "acpterm.acp_agent.spawn_stdio_transport",
        lambda *_a, **_k: _FakeTransport(),
    )
    monkeypatch.setattr(
        "acpterm.acp_agent.ClientSideConnection", lambda *_a, **_k: conn
    )
    monkeypatch.setattr(
        "acpterm.acp_agent.resolve_agent_command", lambda _name: ["fake-agent"]
    )
    monkeypatch.setattr("acpterm.auth.has_auth_configured", lambda _name: False)
    monkeypatch.setattr("acpterm.agent_cache.store", lambda *_a, **_k: None)
    monkeypatch.setattr("acpterm.config.Config.load", lambda: _NoConfig())


def _init_response(
    *, resume: object | None = None, load_session: bool | None = None
) -> acp_schema.InitializeResponse:
    return acp_schema.InitializeResponse(
        protocol_version=1,
        agent_capabilities=acp_schema.AgentCapabilities(
            load_session=load_session,
            session_capabilities=acp_schema.SessionCapabilities(resume=resume),
        ),
    )


def _session_response(session_id: str = "new-session") -> SimpleNamespace:
    return SimpleNamespace(session_id=session_id, config_options=[])


def _agent_with_conn() -> tuple[ACPAgent, _FakeConnection]:
    agent = ACPAgent(project_root=Path("."), silent=True)
    conn = _FakeConnection()
    agent._conn = cast(Any, conn)
    agent._session_id = "session-1"
    return agent, conn


def test_supports_resume_reads_session_capabilities() -> None:
    assert _supports_resume(_init_response()) is False
    assert (
        _supports_resume(_init_response(resume=acp_schema.SessionResumeCapabilities()))
        is True
    )
    assert (
        _supports_resume(
            SimpleNamespace(
                agent_capabilities=SimpleNamespace(
                    session_capabilities=SimpleNamespace(resume={})
                )
            )
        )
        is True
    )
    assert _supports_resume(SimpleNamespace()) is False


def test_supports_load_session_reads_agent_capabilities() -> None:
    assert _supports_load_session(_init_response()) is False
    assert _supports_load_session(_init_response(load_session=True)) is True
    assert _supports_load_session(_init_response(load_session=False)) is False
    assert (
        _supports_load_session(
            SimpleNamespace(agentCapabilities=SimpleNamespace(loadSession=True))
        )
        is True
    )
    assert _supports_load_session(SimpleNamespace()) is False


def test_has_model_config_option_detects_model_entry() -> None:
    assert _has_model_config_option(None) is False
    assert _has_model_config_option(SimpleNamespace(config_options=[])) is False
    assert (
        _has_model_config_option(
            SimpleNamespace(
                config_options=[SimpleNamespace(id="mode")],
            )
        )
        is False
    )
    assert (
        _has_model_config_option(
            SimpleNamespace(
                config_options=[
                    SimpleNamespace(id="mode"),
                    SimpleNamespace(id="model"),
                ],
            )
        )
        is True
    )
    assert (
        _has_model_config_option(SimpleNamespace(configOptions=[{"id": "model"}]))
        is True
    )


def test_is_method_not_found_matches_only_32601() -> None:
    assert _is_method_not_found(RequestError.method_not_found("x")) is True
    assert _is_method_not_found(RequestError(-32000, "auth required")) is False
    assert _is_method_not_found(RuntimeError("boom")) is False


@pytest.mark.asyncio
async def test_start_without_resume_or_load_support_uses_new_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _FakeConnection()
    conn.initialize.return_value = _init_response()
    conn.new_session.return_value = _session_response("fresh")
    _install_start_harness(monkeypatch, conn)
    monkeypatch.setattr(
        "acpterm.acp_agent.get_saved_session", lambda *_args: "saved-session"
    )
    removed: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        "acpterm.session_store.remove", lambda *args: removed.append(args)
    )

    agent = ACPAgent(project_root=tmp_path, silent=True)
    await agent.start()

    conn.resume_session.assert_not_awaited()
    conn.load_session.assert_not_awaited()
    conn.new_session.assert_awaited_once()
    assert agent.session_id == "fresh"
    assert removed == [("opencode", str(tmp_path.absolute()), "default")]


@pytest.mark.asyncio
async def test_start_loads_session_when_only_load_is_advertised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _FakeConnection()
    conn.initialize.return_value = _init_response(load_session=True)
    conn.load_session.return_value = _session_response("saved-session")
    _install_start_harness(monkeypatch, conn)

    agent = ACPAgent(project_root=tmp_path, silent=True)
    await agent.start(target="saved-session")

    conn.load_session.assert_awaited_once()
    conn.resume_session.assert_not_awaited()
    conn.new_session.assert_not_awaited()
    assert agent.session_id == "saved-session"


@pytest.mark.asyncio
async def test_start_resumes_session_when_resume_is_advertised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _FakeConnection()
    conn.initialize.return_value = _init_response(
        resume=acp_schema.SessionResumeCapabilities(), load_session=True
    )
    conn.resume_session.return_value = _session_response("saved-session")
    _install_start_harness(monkeypatch, conn)

    agent = ACPAgent(project_root=tmp_path, silent=True)
    await agent.start(target="saved-session")

    conn.resume_session.assert_awaited_once()
    conn.load_session.assert_not_awaited()
    conn.new_session.assert_not_awaited()
    assert agent.session_id == "saved-session"


@pytest.mark.asyncio
async def test_start_falls_back_to_new_session_when_resume_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _FakeConnection()
    conn.initialize.return_value = _init_response(
        resume=acp_schema.SessionResumeCapabilities()
    )
    conn.resume_session.side_effect = RequestError.method_not_found("session/resume")
    conn.new_session.return_value = _session_response("fresh")
    _install_start_harness(monkeypatch, conn)
    removed: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        "acpterm.session_store.remove", lambda *args: removed.append(args)
    )

    agent = ACPAgent(project_root=tmp_path, silent=True)
    await agent.start(target="saved-session")

    conn.new_session.assert_awaited_once()
    assert agent.session_id == "fresh"
    assert removed == [("opencode", str(tmp_path.absolute()), "default")]


@pytest.mark.asyncio
async def test_set_model_uses_config_option_when_advertised() -> None:
    agent, conn = _agent_with_conn()
    agent._model_uses_config_option = True

    await agent.set_model("gpt-5")

    conn.set_config_option.assert_awaited_once_with(
        config_id="model", session_id="session-1", value="gpt-5"
    )
    conn._conn.send_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_model_uses_set_model_when_config_option_absent() -> None:
    agent, conn = _agent_with_conn()
    agent._model_uses_config_option = False

    await agent.set_model("gpt-5")

    conn._conn.send_request.assert_awaited_once_with(
        "session/set_model",
        {"sessionId": "session-1", "modelId": "gpt-5"},
    )
    conn.set_config_option.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_model_falls_back_when_config_option_is_missing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent, conn = _agent_with_conn()
    agent._model_uses_config_option = True
    conn.set_config_option.side_effect = RequestError.method_not_found(
        "session/set_config_option"
    )

    await agent.set_model("gpt-5")

    conn._conn.send_request.assert_awaited_once_with(
        "session/set_model",
        {"sessionId": "session-1", "modelId": "gpt-5"},
    )
    assert "session/set_config_option" in caplog.text


@pytest.mark.asyncio
async def test_set_model_falls_back_to_config_option_when_set_model_missing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent, conn = _agent_with_conn()
    agent._model_uses_config_option = False
    conn._conn.send_request.side_effect = RequestError.method_not_found(
        "session/set_model"
    )

    await agent.set_model("gpt-5")

    conn.set_config_option.assert_awaited_once_with(
        config_id="model", session_id="session-1", value="gpt-5"
    )
    assert "session/set_model" in caplog.text


@pytest.mark.asyncio
async def test_set_model_reraises_when_both_paths_fail() -> None:
    agent, conn = _agent_with_conn()
    agent._model_uses_config_option = True
    conn.set_config_option.side_effect = RequestError.method_not_found(
        "session/set_config_option"
    )
    conn._conn.send_request.side_effect = RequestError.method_not_found(
        "session/set_model"
    )

    with pytest.raises(RequestError):
        await agent.set_model("gpt-5")


@pytest.mark.asyncio
async def test_set_model_does_not_fall_back_on_unrelated_errors() -> None:
    agent, conn = _agent_with_conn()
    agent._model_uses_config_option = True
    conn.set_config_option.side_effect = RequestError(-32000, "auth required")

    with pytest.raises(RequestError):
        await agent.set_model("gpt-5")

    conn._conn.send_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_model_with_unknown_capability_trials_config_option() -> None:
    agent, conn = _agent_with_conn()
    agent._model_uses_config_option = None
    conn.set_config_option.side_effect = RequestError.method_not_found(
        "session/set_config_option"
    )

    await agent.set_model("gpt-5")

    conn._conn.send_request.assert_awaited_once_with(
        "session/set_model",
        {"sessionId": "session-1", "modelId": "gpt-5"},
    )
