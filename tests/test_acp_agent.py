from __future__ import annotations

from pathlib import Path

import pytest

from acpterm.acp_agent import ACPAgent, AgentClient


class _SpawnShortCircuit(RuntimeError):
    """Raised to short-circuit start() after spawn_stdio_transport is invoked."""


@pytest.mark.asyncio
async def test_write_text_file_creates_parent_directories(tmp_path: Path) -> None:
    client = AgentClient(project_root=tmp_path)
    path = "sub/dir/file.txt"
    content = "hello\nworld"

    await client.write_text_file(session_id="session", path=path, content=content)

    written = tmp_path / path
    assert written.read_text() == content


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
