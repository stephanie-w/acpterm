from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from acp import schema as acp_schema
from acp.exceptions import RequestError
import pytest
from rich.console import Console

from acpterm.acp_agent import (
    _has_model_config_option,
    _is_method_not_found,
    _supports_load_session,
    _supports_resume,
    ACPAgent,
    AgentClient,
)
from acpterm.cli import _fetch_and_cache_agent_info
from acpterm.hooks import HookDefinition
from acpterm.transcript import TranscriptRecorder


class _SpawnShortCircuit(RuntimeError):
    """Raised to short-circuit start() after spawn_stdio_transport is invoked."""


def _make_tool_call(
    kind: str, title: str = "Test tool", name: str | None = None
) -> MagicMock:
    tc = MagicMock()
    tc.title = title
    tc.name = name
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
async def test_write_text_file_silent(tmp_path: Path) -> None:
    client = AgentClient(silent=True, project_root=tmp_path)
    path = "nested/dir/file.txt"

    await client.write_text_file(session_id="session", path=path, content="kept")

    assert (tmp_path / path).read_text() == "kept"


@pytest.mark.asyncio
async def test_read_text_file_silent(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("alpha\nbeta")
    client = AgentClient(silent=True, project_root=tmp_path)

    resp = await client.read_text_file(session_id="session", path="notes.txt")

    assert resp.content == "alpha\nbeta"


@pytest.mark.asyncio
async def test_read_text_file_limit_without_line(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("\n".join(str(i) for i in range(1, 11)))
    client = AgentClient(project_root=tmp_path)

    resp = await client.read_text_file(session_id="session", path="notes.txt", limit=3)

    assert resp.content == "1\n2\n3"


@pytest.mark.asyncio
async def test_read_text_file_windowed_truncation(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("\n".join(["a", "b", "c", "d", "e"]))
    client = AgentClient(project_root=tmp_path, max_read_lines=3)

    resp = await client.read_text_file(session_id="session", path="notes.txt")

    assert resp.content == (
        "a\nb\nc"
        "\n\n[File truncated: showing 3 of 5 lines. "
        "Use line and limit parameters to paginate.]"
    )


@pytest.mark.asyncio
async def test_read_text_file_under_max_lines(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("a\nb\nc")
    client = AgentClient(project_root=tmp_path, max_read_lines=5)

    resp = await client.read_text_file(session_id="session", path="notes.txt")

    assert resp.content == "a\nb\nc"


@pytest.mark.asyncio
async def test_read_text_file_unbounded_when_max_lines_none(tmp_path: Path) -> None:
    content = "\n".join(f"line-{i}" for i in range(5000))
    (tmp_path / "big.txt").write_text(content)
    client = AgentClient(project_root=tmp_path, max_read_lines=None)

    resp = await client.read_text_file(session_id="session", path="big.txt")

    assert resp.content == content


@pytest.mark.parametrize("max_read_lines", [-1, -2000])
def test_negative_max_read_lines_rejected(tmp_path: Path, max_read_lines: int) -> None:
    with pytest.raises(ValueError, match="max_read_lines"):
        AgentClient(project_root=tmp_path, max_read_lines=max_read_lines)


@pytest.mark.parametrize("max_read_lines", [-1, -2000])
def test_acp_agent_negative_max_read_lines_rejected(
    tmp_path: Path, max_read_lines: int
) -> None:
    with pytest.raises(ValueError, match="max_read_lines"):
        ACPAgent(project_root=tmp_path, max_read_lines=max_read_lines)


@pytest.mark.asyncio
async def test_read_text_file_empty_and_zero_limit(tmp_path: Path) -> None:
    (tmp_path / "empty.txt").write_text("")
    (tmp_path / "trailing.txt").write_text("a\nb\n")
    client = AgentClient(project_root=tmp_path)

    empty_resp = await client.read_text_file(session_id="session", path="empty.txt")
    assert empty_resp.content == ""

    zero_resp = await client.read_text_file(
        session_id="session", path="trailing.txt", limit=0
    )
    assert zero_resp.content == ""

    trailing_resp = await client.read_text_file(
        session_id="session", path="trailing.txt"
    )
    assert trailing_resp.content == "a\nb"


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [-1, -10])
async def test_read_text_file_rejects_negative_limit(
    tmp_path: Path, limit: int
) -> None:
    (tmp_path / "notes.txt").write_text("a\nb\nc")
    client = AgentClient(project_root=tmp_path)

    with pytest.raises(ValueError, match="limit"):
        await client.read_text_file(session_id="session", path="notes.txt", limit=limit)


@pytest.mark.asyncio
async def test_read_text_file_zero_limit_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("a\nb\nc")
    client = AgentClient(project_root=tmp_path)

    resp = await client.read_text_file(session_id="session", path="notes.txt", limit=0)

    assert resp.content == ""


@pytest.mark.asyncio
async def test_start_advertises_terminal_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _FakeConnection()
    conn.initialize.return_value = _init_response()
    conn.new_session.return_value = _session_response("fresh")
    _install_start_harness(monkeypatch, conn)

    agent = ACPAgent(project_root=tmp_path, silent=True)
    await agent.start(load_existing=False)

    call = conn.initialize.await_args
    assert call is not None
    capabilities = call.kwargs["client_capabilities"]
    assert capabilities.terminal is False


class _FakeCachingConnection:
    def __init__(self) -> None:
        self.initialize = AsyncMock()
        self.new_session = AsyncMock(
            return_value=SimpleNamespace(session_id=None, config_options=[], modes=None)
        )
        self.close_session = AsyncMock()
        self.close = AsyncMock()


class _FakeInfoTransport:
    async def __aenter__(self) -> tuple[object, object, None]:
        return object(), object(), None

    async def __aexit__(self, *_exc: object) -> bool:
        return False


@pytest.mark.asyncio
async def test_caching_client_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeCachingConnection()
    monkeypatch.setattr(
        "acp.client.connection.ClientSideConnection", lambda *_a, **_k: conn
    )
    monkeypatch.setattr(
        "acp.transports.spawn_stdio_transport", lambda *_a, **_k: _FakeInfoTransport()
    )
    monkeypatch.setattr(
        "acpterm.config.resolve_agent_command", lambda _name: ["fake-agent"]
    )
    monkeypatch.setattr("acpterm.cli.agent_cache.store", lambda *_a, **_k: None)
    monkeypatch.setattr("acpterm.cli.asyncio.sleep", AsyncMock())

    await _fetch_and_cache_agent_info("fake-agent")

    call = conn.initialize.await_args
    assert call is not None
    capabilities = call.kwargs["client_capabilities"]
    assert capabilities.terminal is False


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
        self.prompt = AsyncMock()
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
    *,
    resume: acp_schema.SessionResumeCapabilities | dict[str, Any] | None = None,
    load_session: bool | None = None,
) -> acp_schema.InitializeResponse:
    return acp_schema.InitializeResponse(
        protocol_version=1,
        agent_capabilities=acp_schema.AgentCapabilities(
            load_session=load_session,
            session_capabilities=acp_schema.SessionCapabilities(resume=resume),
        ),
    )


def _session_response(session_id: str = "new-session") -> acp_schema.NewSessionResponse:
    return acp_schema.NewSessionResponse(session_id=session_id, config_options=[])


def _agent_with_conn() -> tuple[ACPAgent, _FakeConnection]:
    agent = ACPAgent(project_root=Path("."), silent=True)
    conn = _FakeConnection()
    agent._conn = cast(Any, conn)
    agent._session_id = "session-1"
    return agent, conn


def _select_option(
    option_id: str,
    *,
    current_value: str = "",
    options: list[acp_schema.SessionConfigSelectOption] | None = None,
) -> acp_schema.SessionConfigOptionSelect:
    return acp_schema.SessionConfigOptionSelect(
        id=option_id,
        name=option_id.title(),
        current_value=current_value,
        options=options or [],
    )


def test_supports_resume_reads_session_capabilities() -> None:
    assert _supports_resume(_init_response()) is False
    assert (
        _supports_resume(_init_response(resume=acp_schema.SessionResumeCapabilities()))
        is True
    )
    assert (
        _supports_resume(
            acp_schema.InitializeResponse(
                protocol_version=1,
                agent_capabilities=acp_schema.AgentCapabilities(
                    session_capabilities=acp_schema.SessionCapabilities(
                        resume=acp_schema.SessionResumeCapabilities()
                    )
                ),
            )
        )
        is True
    )
    assert _supports_resume(None) is False


def test_supports_load_session_reads_agent_capabilities() -> None:
    assert _supports_load_session(_init_response()) is False
    assert _supports_load_session(_init_response(load_session=True)) is True
    assert _supports_load_session(_init_response(load_session=False)) is False
    assert _supports_load_session(None) is False


def test_has_model_config_option_detects_model_entry() -> None:
    assert _has_model_config_option(None) is False
    assert (
        _has_model_config_option(acp_schema.NewSessionResponse(session_id="session"))
        is False
    )
    assert (
        _has_model_config_option(
            acp_schema.NewSessionResponse(
                session_id="session",
                config_options=[_select_option("mode")],
            )
        )
        is False
    )
    assert (
        _has_model_config_option(
            acp_schema.NewSessionResponse(
                session_id="session",
                config_options=[_select_option("mode"), _select_option("model")],
            )
        )
        is True
    )


def test_agent_cache_store_preserves_boolean_and_mode_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acpterm import agent_cache

    monkeypatch.setattr(agent_cache, "CACHE_FILE", tmp_path / "agent_cache.json")

    agent_cache.store(
        "fake-agent",
        config_options=[
            acp_schema.SessionConfigOptionBoolean(
                id="auto",
                name="Auto",
                type="boolean",
                current_value=False,
            ),
            acp_schema.SessionConfigOptionSelect(
                id="model",
                name="Model",
                current_value="gpt-5",
                options=[
                    acp_schema.SessionConfigSelectOption(value="gpt-5", name="GPT-5")
                ],
            ),
        ],
        modes=acp_schema.SessionModeState(
            current_mode_id="build",
            available_modes=[acp_schema.SessionMode(id="build", name="Build")],
        ),
        commands=[acp_schema.AvailableCommand(name="deploy", description="Deploy app")],
    )

    config_options = agent_cache.get_config_options("fake-agent")
    assert config_options is not None
    assert config_options[0]["current_value"] is False
    assert config_options[1]["current_value"] == "gpt-5"
    assert config_options[1]["options"] == [{"id": "gpt-5", "name": "GPT-5"}]

    modes = agent_cache.get_modes("fake-agent")
    assert modes is not None
    assert modes["current_mode_id"] == "build"
    assert modes["available_modes"] == [
        {"id": "build", "name": "Build", "description": None}
    ]

    commands = agent_cache.get_commands("fake-agent")
    assert commands == [{"name": "deploy", "description": "Deploy app"}]


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


def _prompt_response(
    *, usage: acp_schema.Usage | None = None
) -> acp_schema.PromptResponse:
    return acp_schema.PromptResponse(stop_reason="end_turn", usage=usage)


def _usage_tokens() -> acp_schema.Usage:
    return acp_schema.Usage(
        total_tokens=150,
        input_tokens=100,
        output_tokens=40,
        thought_tokens=10,
    )


@pytest.mark.asyncio
async def test_send_prompt_records_usage() -> None:
    agent, conn = _agent_with_conn()
    recorder = TranscriptRecorder("prompt")
    agent._transcript_recorder = recorder
    conn.prompt = AsyncMock(return_value=_prompt_response(usage=_usage_tokens()))

    await agent.send_prompt("prompt")

    assert recorder.usage == {
        "total_tokens": 150,
        "input_tokens": 100,
        "output_tokens": 40,
        "thought_tokens": 10,
    }


@pytest.mark.asyncio
async def test_send_prompt_preserves_existing_cost_and_usage_update() -> None:
    agent, conn = _agent_with_conn()
    recorder = TranscriptRecorder("prompt")
    cost = SimpleNamespace(amount=0.42, currency="USD")
    recorder.set_usage({"used": 100, "size": 1000, "cost": cost})
    agent._transcript_recorder = recorder
    conn.prompt = AsyncMock(return_value=_prompt_response(usage=_usage_tokens()))

    await agent.send_prompt("prompt")

    assert recorder.usage is not None
    assert recorder.usage["used"] == 100
    assert recorder.usage["size"] == 1000
    assert recorder.usage["cost"] is cost
    assert recorder.usage["total_tokens"] == 150
    assert recorder.usage["input_tokens"] == 100
    assert recorder.usage["output_tokens"] == 40


@pytest.mark.asyncio
async def test_send_prompt_leaves_usage_none_when_resp_usage_is_none() -> None:
    agent, conn = _agent_with_conn()
    recorder = TranscriptRecorder("prompt")
    cost = SimpleNamespace(amount=0.42, currency="USD")
    recorder.set_usage({"used": 100, "size": 1000, "cost": cost})
    agent._transcript_recorder = recorder
    conn.prompt = AsyncMock(return_value=_prompt_response(usage=None))

    await agent.send_prompt("prompt")

    assert recorder.usage == {"used": 100, "size": 1000, "cost": cost}


@pytest.mark.asyncio
async def test_send_prompt_without_recorder_handles_resp_usage() -> None:
    agent, conn = _agent_with_conn()
    agent._transcript_recorder = None
    conn.prompt = AsyncMock(return_value=_prompt_response(usage=_usage_tokens()))

    stop_reason = await agent.send_prompt("prompt")

    assert stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_send_prompt_passes_usage_to_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, conn = _agent_with_conn()
    agent._transcript_recorder = None
    conn.prompt = AsyncMock(return_value=_prompt_response(usage=_usage_tokens()))
    format_stop_reason = MagicMock()
    monkeypatch.setattr("acpterm.acp_agent.format_stop_reason", format_stop_reason)

    await agent.send_prompt("prompt")

    format_stop_reason.assert_called_once_with(
        "end_turn",
        usage={
            "total_tokens": 150,
            "input_tokens": 100,
            "output_tokens": 40,
            "thought_tokens": 10,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", ["hello", ""])
async def test_send_prompt_builds_text_content_block(prompt: str) -> None:
    agent, conn = _agent_with_conn()
    conn.prompt = AsyncMock(return_value=_prompt_response())

    await agent.send_prompt(prompt)

    call = conn.prompt.await_args
    assert call is not None
    session_id, blocks = call.args
    assert session_id == "session-1"
    assert len(blocks) == 1
    assert isinstance(blocks[0], acp_schema.TextContentBlock)
    assert blocks[0].type == "text"
    assert blocks[0].text == prompt


@pytest.mark.asyncio
async def test_send_prompt_builds_resource_content_blocks(tmp_path: Path) -> None:
    agent, conn = _agent_with_conn()
    conn.prompt = AsyncMock(return_value=_prompt_response())
    existing = tmp_path / "notes.txt"
    existing.write_text("abc")
    missing = tmp_path / "missing.txt"

    await agent.send_prompt("summarize", resources=[existing, missing])

    call = conn.prompt.await_args
    assert call is not None
    _, blocks = call.args
    assert len(blocks) == 3
    assert isinstance(blocks[1], acp_schema.ResourceContentBlock)
    assert blocks[1].type == "resource_link"
    assert blocks[1].name == "notes.txt"
    assert blocks[1].uri == f"file://{existing.absolute()}"
    assert blocks[1].size == 3
    assert isinstance(blocks[2], acp_schema.ResourceContentBlock)
    assert blocks[2].name == "missing.txt"
    assert blocks[2].uri == f"file://{missing.absolute()}"
    assert blocks[2].size is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("conn", "session_id"),
    [(None, "session-1"), (cast(Any, _FakeConnection()), None)],
)
async def test_send_prompt_requires_started_agent(
    conn: _FakeConnection | None, session_id: str | None
) -> None:
    agent = ACPAgent(project_root=Path("."), silent=True)
    agent._conn = cast(Any, conn)
    agent._session_id = session_id

    with pytest.raises(RuntimeError, match="Agent not started"):
        await agent.send_prompt("hello")


def test_transcript_recorder_to_markdown_with_token_usage() -> None:
    recorder = TranscriptRecorder("prompt")
    recorder.set_stop_reason("end_turn")
    recorder.set_usage(
        {
            "total_tokens": 150,
            "input_tokens": 100,
            "output_tokens": 40,
            "thought_tokens": 10,
        }
    )

    markdown = recorder.to_markdown()

    assert "- **Token Usage**: 150 tokens" in markdown


def test_transcript_recorder_derives_token_total_from_breakdown() -> None:
    recorder = TranscriptRecorder("prompt")
    recorder.set_usage({"input_tokens": 100, "output_tokens": 40, "thought_tokens": 10})

    markdown = recorder.to_markdown()

    assert "- **Token Usage**: 150 tokens (input: 100, output: 40, thought: 10)" in (
        markdown
    )


def test_transcript_recorder_merges_usage_without_clobbering_cost() -> None:
    recorder = TranscriptRecorder("prompt")
    cost = SimpleNamespace(amount=0.42, currency="USD")
    recorder.set_usage({"used": 100, "size": 1000, "cost": cost})

    recorder.set_usage({"total_tokens": 150, "input_tokens": 100, "output_tokens": 40})
    recorder.set_usage({"used": None, "size": None, "cost": None})

    assert recorder.usage == {
        "used": 100,
        "size": 1000,
        "cost": cost,
        "total_tokens": 150,
        "input_tokens": 100,
        "output_tokens": 40,
    }


def test_transcript_recorder_keeps_token_usage_when_usage_update_arrives_late() -> None:
    recorder = TranscriptRecorder("prompt")

    recorder.set_usage({"total_tokens": 150, "input_tokens": 100, "output_tokens": 40})
    recorder.set_usage({"used": 100, "size": 1000, "cost": None})

    assert recorder.usage == {
        "total_tokens": 150,
        "input_tokens": 100,
        "output_tokens": 40,
        "used": 100,
        "size": 1000,
        "cost": None,
    }


def test_transcript_recorder_skips_metadata_for_all_none_usage() -> None:
    recorder = TranscriptRecorder("prompt")
    recorder.set_usage({"used": None, "size": None, "cost": None})

    markdown = recorder.to_markdown()

    assert "## Metadata" not in markdown


@pytest.mark.parametrize(
    "bad_total",
    ["150", 150.5, float("nan"), float("inf"), True, -1],
)
def test_transcript_recorder_derives_total_when_total_is_malformed(
    bad_total: object,
) -> None:
    recorder = TranscriptRecorder("prompt")
    recorder.set_usage({"total_tokens": bad_total, "input_tokens": 100})

    markdown = recorder.to_markdown()

    assert "- **Token Usage**: 100 tokens (input: 100)" in markdown


def test_transcript_recorder_skips_malformed_token_breakdown() -> None:
    recorder = TranscriptRecorder("prompt")
    recorder.set_usage(
        {
            "total_tokens": 150,
            "input_tokens": "100",
            "output_tokens": 40,
            "thought_tokens": float("nan"),
        }
    )

    markdown = recorder.to_markdown()

    assert "- **Token Usage**: 150 tokens (output: 40)" in markdown


@pytest.mark.parametrize(
    "bad_value",
    ["100", 100.5, float("nan"), float("inf"), True, -1],
)
def test_transcript_recorder_skips_malformed_context_usage(bad_value: object) -> None:
    for key in ("used", "size"):
        recorder = TranscriptRecorder("prompt")
        usage: dict[str, Any] = {"used": 100, "size": 1000}
        usage[key] = bad_value
        recorder.set_usage(usage)

        markdown = recorder.to_markdown()

        assert "**Context Usage**" not in markdown


@pytest.mark.parametrize("status", ["completed", "failed", "error", "cancelled"])
def test_update_tool_call_records_duration_on_terminal_status(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    clock = iter([100.0, 101.2346])
    monkeypatch.setattr("acpterm.transcript.time.monotonic", lambda: next(clock))
    recorder = TranscriptRecorder("prompt")

    recorder.add_tool_call("tc-1", "Run", "execute")

    assert recorder.tool_calls["tc-1"]["duration_seconds"] is None
    assert "tc-1" in recorder._tool_call_starts

    recorder.update_tool_call("tc-1", status=status)

    assert recorder.tool_calls["tc-1"]["duration_seconds"] == 1.235


@pytest.mark.parametrize("status", ["pending", "in_progress", "", None])
def test_update_tool_call_skips_duration_for_non_terminal_status(
    monkeypatch: pytest.MonkeyPatch, status: str | None
) -> None:
    clock = iter([100.0, 105.0])
    monkeypatch.setattr("acpterm.transcript.time.monotonic", lambda: next(clock))
    recorder = TranscriptRecorder("prompt")
    recorder.add_tool_call("tc-1", "Run", "execute")

    recorder.update_tool_call("tc-1", status=status)

    assert recorder.tool_calls["tc-1"]["duration_seconds"] is None


def test_update_tool_call_terminal_on_unseen_records_zero_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("acpterm.transcript.time.monotonic", lambda: 500.0)
    recorder = TranscriptRecorder("prompt")

    recorder.update_tool_call("tc-1", status="completed", title="Run")

    assert recorder.tool_calls["tc-1"]["duration_seconds"] == 0.0
    assert recorder.tool_calls["tc-1"]["title"] == "Run"
    assert "tc-1" in recorder._tool_call_starts


def test_tool_call_entry_exposes_only_clean_fields() -> None:
    recorder = TranscriptRecorder("prompt")
    recorder.add_tool_call("tc-1", "Run", "execute", raw_input={"command": "ls"})

    recorder.update_tool_call(
        "tc-1", status="completed", raw_input={"command": "ls -la"}
    )

    assert set(recorder.tool_calls["tc-1"]) == {
        "name",
        "title",
        "kind",
        "status",
        "content",
        "raw_input",
        "raw_output",
        "duration_seconds",
    }
    assert recorder.tool_calls["tc-1"]["name"] is None
    assert "tc-1" in recorder._tool_call_starts


def test_update_tool_call_name_preservation() -> None:
    recorder = TranscriptRecorder("prompt")
    recorder.add_tool_call("tc-1", "Run", "execute", name="bash")

    recorder.update_tool_call("tc-1", status="in_progress")
    assert recorder.tool_calls["tc-1"]["name"] == "bash"

    recorder.update_tool_call("tc-1", name="shell")
    assert recorder.tool_calls["tc-1"]["name"] == "shell"


def test_tool_call_raw_payload_isolation() -> None:
    raw_input = {"command": "ls", "args": ["-la"]}
    recorder = TranscriptRecorder("prompt")
    recorder.add_tool_call("tc-1", "Run", "execute", raw_input=raw_input)

    raw_input["command"] = "rm"
    raw_input["args"].append("-rf")

    assert recorder.tool_calls["tc-1"]["raw_input"] == {
        "command": "ls",
        "args": ["-la"],
    }

    updated_input = {"command": "pwd"}
    raw_output = {"stdout": ["ok"]}
    recorder.update_tool_call("tc-1", raw_input=updated_input, raw_output=raw_output)

    updated_input["command"] = "mutated"
    raw_output["stdout"].append("mutated")

    assert recorder.tool_calls["tc-1"]["raw_input"] == {"command": "pwd"}
    assert recorder.tool_calls["tc-1"]["raw_output"] == {"stdout": ["ok"]}


def test_to_markdown_tool_call_header() -> None:
    recorder = TranscriptRecorder("prompt")
    recorder.add_tool_call("tc-1", "Run", "execute", name="bash")
    recorder.update_tool_call("tc-1", status="completed")
    recorder.add_tool_call("tc-2", "Read", "read")
    recorder.add_tool_call("tc-3", "Edit", "edit", name="")

    markdown = recorder.to_markdown()

    assert "### `Run` [bash] (execute) - completed" in markdown
    assert "### `Read` (read) - pending" in markdown
    assert "### `Edit` (edit) - pending" in markdown


def test_to_markdown_non_primitive_payload() -> None:
    recorder = TranscriptRecorder("prompt")
    recorder.add_tool_call(
        "tc-1", "Read", "read", raw_input={"path": Path("src/file.py")}
    )
    recorder.update_tool_call("tc-1", raw_output={"resolved": Path("src/output.py")})

    markdown = recorder.to_markdown()

    assert '"path": "src/file.py"' in markdown
    assert '"resolved": "src/output.py"' in markdown


def test_update_tool_call_replaces_raw_input_when_non_empty() -> None:
    recorder = TranscriptRecorder("prompt")
    recorder.add_tool_call("tc-1", "Run", "execute", raw_input={"command": "ls"})

    recorder.update_tool_call("tc-1", raw_input={"command": "ls -la"})

    assert recorder.tool_calls["tc-1"]["raw_input"] == {"command": "ls -la"}


@pytest.mark.parametrize("empty_raw_input", [None, {}, "", []])
def test_update_tool_call_ignores_empty_raw_input(empty_raw_input: object) -> None:
    recorder = TranscriptRecorder("prompt")
    recorder.add_tool_call("tc-1", "Run", "execute", raw_input={"command": "ls"})

    recorder.update_tool_call("tc-1", raw_input=empty_raw_input)

    assert recorder.tool_calls["tc-1"]["raw_input"] == {"command": "ls"}


@pytest.mark.asyncio
async def test_session_update_forwards_raw_input_on_tool_call_update() -> None:
    recorder = TranscriptRecorder("prompt")
    recorder.add_tool_call("tc-1", "Run", "execute")
    client = AgentClient(silent=True, recorder=recorder)
    update = acp_schema.ToolCallProgress(
        tool_call_id="tc-1",
        status="in_progress",
        raw_input={"command": "ls"},
    )

    await client.session_update("session", update)

    assert recorder.tool_calls["tc-1"]["raw_input"] == {"command": "ls"}


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["tool_call", "tool_call_update"])
async def test_session_update_forwards_tool_call_name(event_type: str) -> None:
    recorder = TranscriptRecorder("prompt")
    if event_type == "tool_call_update":
        recorder.add_tool_call("tc-1", "Run", "execute")
    client = AgentClient(silent=True, recorder=recorder)
    model = (
        acp_schema.ToolCallStart
        if event_type == "tool_call"
        else acp_schema.ToolCallProgress
    )
    update = model(
        tool_call_id="tc-1",
        title="Run",
        kind="execute",
        name="bash",
    )

    await client.session_update("session", update)

    assert recorder.tool_calls["tc-1"]["name"] == "bash"


@pytest.mark.asyncio
async def test_request_permission_includes_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    async def fake_run_permission_hook(
        event: Any, _hooks: Any, *, verbose: bool = False
    ) -> int:
        captured["event"] = event
        return 0

    monkeypatch.setattr("acpterm.hooks.run_permission_hook", fake_run_permission_hook)
    stream = StringIO()
    console = Console(
        file=stream,
        width=200,
        force_terminal=False,
        color_system=None,
        highlight=False,
    )
    monkeypatch.setattr("acpterm.acp_agent._console", console)

    client = AgentClient(
        silent=False,
        permission_hooks=[HookDefinition(name="capture", on="permission")],
        project_root=tmp_path,
    )
    tool_call = _make_tool_call("execute", title="Run shell", name="bash")

    resp = await client.request_permission(
        session_id="s1", tool_call=tool_call, options=[_make_option("allow")]
    )

    assert captured["event"].extra["name"] == "bash"
    assert "[bash]" in stream.getvalue()
    assert resp.outcome.outcome == "selected"


@pytest.mark.asyncio
async def test_request_permission_escapes_tool_call_markup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = StringIO()
    console = Console(
        file=stream,
        width=200,
        force_terminal=False,
        color_system=None,
        highlight=False,
    )
    monkeypatch.setattr("acpterm.acp_agent._console", console)

    client = AgentClient(silent=False, read_only=True)
    tool_call = _make_tool_call(
        "read",
        title="Read [red]secret[/red]",
        name="[bold]bash[/bold]",
    )

    await client.request_permission(
        session_id="s1", tool_call=tool_call, options=[_make_option("allow")]
    )

    text = stream.getvalue()
    assert "Read [red]secret[/red]" in text
    assert "[bold]bash[/bold]" in text
