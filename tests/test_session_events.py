"""Seam tests for normalized session events and observer dispatch."""

from __future__ import annotations

from io import StringIO
import json
from types import SimpleNamespace
from typing import Any, cast

from acp import schema as acp_schema
import pytest
from rich.console import Console

from acpterm import agent_cache, output
from acpterm.acp_agent import AgentClient
from acpterm.session_events import (
    AvailableCommandsEvent,
    CurrentModeEvent,
    extract_diff_operations,
    extract_text,
    FileOperation,
    format_content_blocks,
    MessageChunkEvent,
    ModelConfigEvent,
    normalize_session_update,
    PlanEvent,
    SessionEvent,
    SessionUpdate,
    ThoughtChunkEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
    UsageEvent,
)
from acpterm.transcript import TranscriptRecorder


@pytest.fixture(autouse=True)
def _reset_output_state() -> Any:
    output._state._usage = None
    output._state._mode_displayed = False
    output._state._model_displayed = False
    output._state.in_stream = False
    output._state.tool_kind = None
    yield
    output._state._usage = None
    output._state._mode_displayed = False
    output._state._model_displayed = False
    output._state.in_stream = False
    output._state.tool_kind = None


@pytest.fixture
def rendered(monkeypatch: pytest.MonkeyPatch) -> StringIO:
    stream = StringIO()
    console = Console(
        file=stream,
        width=200,
        force_terminal=False,
        color_system=None,
        highlight=False,
    )
    monkeypatch.setattr(output, "_console", console)
    return stream


def _model_config_update() -> acp_schema.ConfigOptionUpdate:
    return acp_schema.ConfigOptionUpdate(
        config_options=[
            acp_schema.SessionConfigOptionSelect(
                id="model",
                name="Model",
                current_value="gpt-5",
                options=[
                    acp_schema.SessionConfigSelectOption(value="gpt-5", name="GPT-5")
                ],
            )
        ]
    )


def _plan_update() -> acp_schema.AgentPlanUpdate:
    return acp_schema.AgentPlanUpdate(
        entries=[
            acp_schema.PlanEntry(
                content="Write tests", priority="high", status="in_progress"
            )
        ]
    )


def _commands_update() -> acp_schema.AvailableCommandsUpdate:
    return acp_schema.AvailableCommandsUpdate(
        available_commands=[
            acp_schema.AvailableCommand(name="plan", description="Create a plan")
        ]
    )


@pytest.mark.parametrize(
    ("update", "expected"),
    [
        (
            acp_schema.AgentThoughtChunk(
                content=acp_schema.TextContentBlock(text="thinking"),
                message_id="m1",
            ),
            ThoughtChunkEvent(message_id="m1", text="thinking"),
        ),
        (
            acp_schema.AgentMessageChunk(
                content=acp_schema.TextContentBlock(text="hello"),
                message_id="m2",
            ),
            MessageChunkEvent(message_id="m2", text="hello"),
        ),
        (
            acp_schema.ToolCallStart(
                tool_call_id="tc-1",
                title="",
                kind="execute",
                name="bash",
                raw_input={},
            ),
            ToolCallStartEvent(
                tool_call_id="tc-1",
                title="Unknown tool",
                kind="execute",
                status=None,
                name="bash",
                raw_input=None,
            ),
        ),
        (
            acp_schema.ToolCallProgress(
                tool_call_id="tc-2",
                title="Run shell",
                kind="execute",
                status="completed",
                name="bash",
            ),
            ToolCallProgressEvent(
                tool_call_id="tc-2",
                title="Run shell",
                kind="execute",
                status="completed",
                name="bash",
                content_text=None,
                raw_input=None,
                raw_output=None,
                diff_operations=(),
            ),
        ),
        (
            acp_schema.UsageUpdate(
                used=100,
                size=1000,
                cost=acp_schema.Cost(amount=0.42, currency="USD"),
            ),
            UsageEvent(
                used=100,
                size=1000,
                cost=acp_schema.Cost(amount=0.42, currency="USD"),
            ),
        ),
        (
            acp_schema.CurrentModeUpdate(current_mode_id="build"),
            CurrentModeEvent(current_mode_id="build"),
        ),
        (_model_config_update(), ModelConfigEvent(model="gpt-5")),
        (_plan_update(), PlanEvent(entries=tuple(_plan_update().entries or []))),
        (
            _commands_update(),
            AvailableCommandsEvent(
                commands=list(_commands_update().available_commands)
            ),
        ),
    ],
)
def test_normalize_session_update(
    update: SessionUpdate, expected: SessionEvent
) -> None:
    assert normalize_session_update(update) == expected


def test_normalize_session_update_ignores_unhandled_updates() -> None:
    update = acp_schema.UserMessageChunk(
        content=acp_schema.TextContentBlock(text="from user")
    )

    assert normalize_session_update(update) is None


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        (
            acp_schema.FileEditToolCallContent(path="/repo/new.py", new_text="x"),
            (FileOperation("create", "/repo/new.py"),),
        ),
        (
            acp_schema.FileEditToolCallContent(
                path="/repo/old.py", old_text="old", new_text="new"
            ),
            (FileOperation("modify", "/repo/old.py"),),
        ),
    ],
)
def test_extract_diff_operations_v1(
    block: acp_schema.FileEditToolCallContent, expected: tuple[FileOperation, ...]
) -> None:
    assert extract_diff_operations([block]) == expected


def test_extract_diff_operations_v2_changes() -> None:
    block = SimpleNamespace(
        type="diff",
        path=None,
        changes=[
            SimpleNamespace(operation="create", path="/repo/a.py"),
            SimpleNamespace(operation="modify", path="/repo/b.py"),
        ],
    )

    assert extract_diff_operations([cast(Any, block)]) == (
        FileOperation("create", "/repo/a.py"),
        FileOperation("modify", "/repo/b.py"),
    )


def test_tool_call_progress_normalizes_content_and_diffs() -> None:
    long_text = "x" * 400
    progress = acp_schema.ToolCallProgress(
        tool_call_id="tc-1",
        kind="read",
        content=[
            acp_schema.ContentToolCallContent(
                content=acp_schema.TextContentBlock(text=long_text)
            ),
        ],
    )

    event = cast(ToolCallProgressEvent, normalize_session_update(progress))

    assert event.content_text == format_content_blocks(
        progress.content or [], tool_kind="read"
    )
    assert event.content_text is not None
    assert event.content_text.endswith("...")
    assert len(event.content_text) < len(long_text)


def test_tool_call_progress_normalizes_v2_changes_from_wire_payload() -> None:
    block = SimpleNamespace(
        type="diff",
        path=None,
        changes=[SimpleNamespace(operation="modify", path="/repo/c.py")],
    )
    update = cast(
        SessionUpdate,
        SimpleNamespace(
            session_update="tool_call_update",
            tool_call_id="tc-1",
            title=None,
            kind=None,
            status=None,
            name=None,
            content=[block],
            raw_input=None,
            raw_output=None,
        ),
    )

    event = cast(ToolCallProgressEvent, normalize_session_update(update))

    assert event.diff_operations == (FileOperation("modify", "/repo/c.py"),)


def test_render_session_event_emits_terminal_output(rendered: StringIO) -> None:
    output.render_session_event(ThoughtChunkEvent(message_id="m1", text="pondering"))
    output.render_session_event(CurrentModeEvent(current_mode_id="build"))

    text = rendered.getvalue()
    assert "[thinking] pondering" in text
    assert "[mode] build" in text


@pytest.mark.asyncio
async def test_session_update_dispatches_one_event_to_output_and_recorder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[SessionEvent] = []
    monkeypatch.setattr("acpterm.acp_agent.render_session_event", seen.append)
    recorder = TranscriptRecorder("prompt")
    client = AgentClient(silent=False, recorder=recorder)
    updates = [
        acp_schema.AgentThoughtChunk(
            content=acp_schema.TextContentBlock(text="thinking"), message_id="m1"
        ),
        acp_schema.AgentMessageChunk(
            content=acp_schema.TextContentBlock(text="hello"), message_id="m2"
        ),
    ]

    for update in updates:
        await client.session_update("session", update)

    assert [type(event) for event in seen] == [
        ThoughtChunkEvent,
        MessageChunkEvent,
    ]
    assert recorder.thoughts == ["thinking"]
    assert recorder.messages == ["hello"]


@pytest.mark.asyncio
async def test_session_update_invokes_injected_observers() -> None:
    seen: list[SessionEvent] = []
    client = AgentClient(silent=False, observers=[seen.append])
    update = acp_schema.UsageUpdate(used=1, size=2)

    await client.session_update("session", update)

    assert seen == [UsageEvent(used=1, size=2, cost=None)]


@pytest.mark.asyncio
async def test_available_commands_event_updates_cache_without_output_or_transcript(
    monkeypatch: pytest.MonkeyPatch, rendered: StringIO
) -> None:
    calls: list[tuple[str, list[Any]]] = []
    monkeypatch.setattr(
        agent_cache, "update_commands", lambda agent, cmds: calls.append((agent, cmds))
    )
    recorder = TranscriptRecorder("prompt")
    client = AgentClient(agent_binary="fake-agent", silent=False, recorder=recorder)

    await client.session_update("session", _commands_update())

    assert calls == [("fake-agent", _commands_update().available_commands)]
    assert rendered.getvalue() == ""
    assert recorder.to_markdown().strip().endswith("prompt")


@pytest.mark.asyncio
async def test_available_commands_cache_errors_are_suppressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(_agent: str, _cmds: list[Any]) -> None:
        raise RuntimeError("cache unavailable")

    monkeypatch.setattr(agent_cache, "update_commands", explode)
    client = AgentClient(agent_binary="fake-agent", silent=True)

    await client.session_update("session", _commands_update())


_MODIFY_DIFF = acp_schema.FileEditToolCallContent(
    path="/repo/app.py", old_text="alpha\nbeta\n", new_text="alpha\ngamma\n"
)
_CREATE_DIFF = acp_schema.FileEditToolCallContent(
    path="/repo/new.py", old_text=None, new_text="one\ntwo\n"
)


@pytest.mark.parametrize(
    ("block", "headers", "removed", "added"),
    [
        (_MODIFY_DIFF, ("--- /repo/app.py", "+++ /repo/app.py"), "-beta", "+gamma"),
        (_CREATE_DIFF, ("--- /dev/null", "+++ /repo/new.py"), None, "+one"),
    ],
)
def test_file_edit_blocks_render_as_unified_diff(
    block: acp_schema.FileEditToolCallContent,
    headers: tuple[str, str],
    removed: str | None,
    added: str,
) -> None:
    for rendered in (extract_text(block), format_content_blocks([block])):
        lines = rendered.splitlines()
        assert tuple(lines[:2]) == headers
        assert added in lines
        if removed is not None:
            assert removed in lines
        assert "FileEditToolCallContent(" not in rendered


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        (
            acp_schema.FileEditToolCallContent(
                path="/repo/empty.py", old_text=None, new_text=""
            ),
            "create /repo/empty.py (empty file)",
        ),
        (
            acp_schema.FileEditToolCallContent(
                path="/repo/same.py", old_text="same\n", new_text="same\n"
            ),
            "diff /repo/same.py (no changes)",
        ),
    ],
)
def test_file_edit_blocks_summarize_empty_diffs(
    block: acp_schema.FileEditToolCallContent, expected: str
) -> None:
    assert extract_text(block) == expected
    assert format_content_blocks([block]) == expected


def test_file_edit_blocks_truncate_large_diffs() -> None:
    block = acp_schema.FileEditToolCallContent(
        path="/repo/big.py",
        old_text=None,
        new_text="\n".join(f"line {number}" for number in range(80)),
    )

    lines = extract_text(block).splitlines()

    assert len(lines) == 51
    assert lines[2].startswith("@@")
    assert lines[-1] == "... (33 more lines)"


def test_v2_changes_diff_blocks_render_operations() -> None:
    block = SimpleNamespace(
        type="diff",
        path=None,
        changes=[
            SimpleNamespace(operation="create", path="/repo/a.py"),
            SimpleNamespace(operation="modify", path="/repo/b.py"),
        ],
    )

    expected = "create /repo/a.py\nmodify /repo/b.py"

    assert extract_text(block) == expected
    assert format_content_blocks([cast(Any, block)]) == expected


@pytest.mark.parametrize(
    ("payload", "expected"),
    [({"text": "plain"}, "plain"), ({"content": {"text": "nested"}}, "nested")],
)
def test_extract_text_reads_mapping_text_and_nested_content(
    payload: dict[str, Any], expected: str
) -> None:
    assert extract_text(payload) == expected
    block = SimpleNamespace(type="content", content=payload)
    assert format_content_blocks([cast(Any, block)]) == expected


@pytest.mark.parametrize(
    ("payload", "headers", "removed", "added"),
    [
        (
            {
                "type": "diff",
                "path": "/repo/new.py",
                "old_text": None,
                "new_text": "one\n",
            },
            ("--- /dev/null", "+++ /repo/new.py"),
            None,
            "+one",
        ),
        (
            {"path": "/repo/new.py", "newText": "one\n"},
            ("--- /dev/null", "+++ /repo/new.py"),
            None,
            "+one",
        ),
        (
            {"path": "/repo/app.py", "oldText": "alpha\n", "newText": "beta\n"},
            ("--- /repo/app.py", "+++ /repo/app.py"),
            "-alpha",
            "+beta",
        ),
    ],
)
def test_extract_text_reads_mapping_diff_payloads(
    payload: dict[str, Any],
    headers: tuple[str, str],
    removed: str | None,
    added: str,
) -> None:
    block = SimpleNamespace(type="content", content=payload)

    rendered = format_content_blocks([cast(Any, block)])
    lines = rendered.splitlines()

    assert tuple(lines[:2]) == headers
    assert added in lines
    if removed is not None:
        assert removed in lines
    assert extract_text(payload) == rendered


def test_format_content_blocks_renders_unknown_mappings_as_json() -> None:
    payload = {"zeta": 1, "alpha": {"nested": [True, None]}}
    block = SimpleNamespace(type="content", content=payload)

    rendered = format_content_blocks([cast(Any, block)])

    assert json.loads(rendered) == payload
    assert rendered.startswith('{\n  "alpha"')
    assert "{'zeta'" not in rendered


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"text": None},
        {"content": None},
        {"content": 42},
        {"type": "diff", "path": "", "new_text": "x"},
        {"type": "diff", "path": "/repo/x.py", "new_text": None},
        {"path": "/repo/x.py", "new_text": 3},
        {"path": "/repo/x.py", "newText": "x", "oldText": 7},
    ],
)
def test_extract_text_rejects_invalid_mapping_payloads(payload: dict[str, Any]) -> None:
    assert extract_text(payload) == ""


@pytest.mark.parametrize("payload", [None, 42, [], ""])
def test_extract_text_ignores_non_content_values(payload: Any) -> None:
    assert extract_text(payload) == ""


def test_tool_call_progress_renders_diff_in_output_and_transcript(
    rendered: StringIO,
) -> None:
    progress = acp_schema.ToolCallProgress(
        tool_call_id="tc-diff",
        title="Edit app.py",
        kind="edit",
        status="completed",
        content=[_MODIFY_DIFF],
    )

    event = cast(ToolCallProgressEvent, normalize_session_update(progress))

    assert event.content_text is not None
    assert "--- /repo/app.py" in event.content_text
    assert "+gamma" in event.content_text
    assert event.diff_operations == (FileOperation("modify", "/repo/app.py"),)

    output.render_session_event(event)
    terminal = rendered.getvalue()
    assert "--- /repo/app.py" in terminal
    assert "+gamma" in terminal
    assert "FileEditToolCallContent(" not in terminal

    recorder = TranscriptRecorder("prompt")
    recorder.record_event(event)
    markdown = recorder.to_markdown()
    assert "--- /repo/app.py" in markdown
    assert "+gamma" in markdown
    assert "FileEditToolCallContent(" not in markdown


def test_extract_text_rejects_duck_typed_diff_with_non_str_old_text() -> None:
    diff_obj = SimpleNamespace(
        type="diff",
        path="/repo/app.py",
        old_text=123,
        new_text="hello\n",
    )
    assert extract_text(diff_obj) == ""


def test_render_session_event_escapes_rich_markup_in_content_text(
    rendered: StringIO,
) -> None:
    diff = acp_schema.FileEditToolCallContent(
        type="diff",
        path="/repo/notes[red].txt",
        old_text=None,
        new_text="[bold]x[/bold]\n",
    )
    progress = acp_schema.ToolCallProgress(
        tool_call_id="tc-markup",
        title="Edit notes",
        kind="edit",
        status="completed",
        content=[diff],
    )
    event = cast(ToolCallProgressEvent, normalize_session_update(progress))

    output.render_session_event(event)
    terminal = rendered.getvalue()

    assert "/repo/notes[red].txt" in terminal
    assert "+[bold]x[/bold]" in terminal
