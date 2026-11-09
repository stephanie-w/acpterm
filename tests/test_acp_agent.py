from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from acpterm.acp_agent import ACPAgent, AgentClient


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
