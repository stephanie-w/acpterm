"""Normalized session update events.

This module owns the boundary between ACP wire schemas and the rest of the
application. ``normalize_session_update`` casts a raw ``SessionUpdate`` into a
typed, immutable event; terminal rendering, transcript recording, and command
caching are observers of that single normalized stream and never need to know
about ACP schema classes.

Dependency direction: ``session_events`` imports only ``acp.schema`` and the
standard library, so it stays strictly below ``output``, ``transcript``, and
``acp_agent`` in the module graph.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from acp import schema as acp_schema
from rich.markup import escape


CONTENT_TRUNCATE_LEN = 300

SessionUpdate = (
    acp_schema.UserMessageChunk
    | acp_schema.AgentMessageChunk
    | acp_schema.AgentThoughtChunk
    | acp_schema.ToolCallStart
    | acp_schema.ToolCallProgress
    | acp_schema.AgentPlanUpdate
    | acp_schema.AgentPlanContentUpdate
    | acp_schema.AgentPlanRemovedUpdate
    | acp_schema.AvailableCommandsUpdate
    | acp_schema.CurrentModeUpdate
    | acp_schema.ConfigOptionUpdate
    | acp_schema.SessionInfoUpdate
    | acp_schema.UsageUpdate
    | acp_schema.SessionUpdateNotice
    | acp_schema.SessionUpdateCompactionUpdate
    | acp_schema.SessionUpdateCompactionSummaryChunk
)

ToolContentBlock = (
    acp_schema.ContentToolCallContent
    | acp_schema.FileEditToolCallContent
    | acp_schema.TerminalToolCallContent
)


@dataclass(frozen=True, slots=True)
class FileOperation:
    """A file-system operation extracted from a tool call diff block."""

    operation: str
    path: str


@dataclass(frozen=True, slots=True)
class ThoughtChunkEvent:
    """A streamed chunk of the agent's reasoning."""

    message_id: str | None
    text: str


@dataclass(frozen=True, slots=True)
class MessageChunkEvent:
    """A streamed chunk of the agent's visible response."""

    message_id: str | None
    text: str


@dataclass(frozen=True, slots=True)
class ToolCallStartEvent:
    """The agent announced a new tool call."""

    tool_call_id: str
    title: str
    kind: str | None
    status: str | None
    name: str | None
    raw_input: Any | None


@dataclass(frozen=True, slots=True)
class ToolCallProgressEvent:
    """A progress update for an existing tool call."""

    tool_call_id: str
    title: str | None
    kind: str | None
    status: str | None
    name: str | None
    content_text: str | None
    raw_input: Any | None
    raw_output: Any | None
    diff_operations: tuple[FileOperation, ...]


@dataclass(frozen=True, slots=True)
class UsageEvent:
    """A context-window usage update."""

    used: int | None
    size: int | None
    cost: Any | None


@dataclass(frozen=True, slots=True)
class CurrentModeEvent:
    """The agent switched session mode."""

    current_mode_id: str | None


@dataclass(frozen=True, slots=True)
class ModelConfigEvent:
    """The model reported by the session config options."""

    model: str | None


@dataclass(frozen=True, slots=True)
class PlanEvent:
    """A full snapshot of the agent's plan."""

    entries: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class AvailableCommandsEvent:
    """The agent advertised its available slash commands."""

    commands: list[Any]


type SessionEvent = (
    ThoughtChunkEvent
    | MessageChunkEvent
    | ToolCallStartEvent
    | ToolCallProgressEvent
    | UsageEvent
    | CurrentModeEvent
    | ModelConfigEvent
    | PlanEvent
    | AvailableCommandsEvent
)

type SessionEventObserver = Callable[[SessionEvent], None]


def extract_text(content: Any) -> str:
    """Extract plain text from an ACP content block."""
    if isinstance(content, acp_schema.TextContentBlock):
        return content.text or ""
    if hasattr(content, "content"):
        return extract_text(content.content)
    text_value = getattr(content, "text", None)
    if text_value is not None:
        return str(text_value)
    return ""


def format_content_blocks(
    content_blocks: list[ToolContentBlock],
    *,
    tool_kind: str | None = None,
) -> str:
    """Render ACP tool-call content blocks as plain text.

    ``tool_kind`` controls read-tool truncation; it is passed explicitly so
    formatting has no hidden dependency on terminal rendering state.
    """
    parts: list[str] = []
    for block in content_blocks:
        # Handle terminal content references (type="terminal", terminal_id=...)
        if block.type == "terminal":
            terminal_id = block.terminal_id
            label = escape(f"terminal {terminal_id or 'unknown'}")
            parts.append(f"\\[{label}]")
            continue

        if hasattr(block, "content"):
            inner = block.content
        else:
            inner = block
        text = extract_text(inner)
        if text:
            if tool_kind == "read" and len(text) > CONTENT_TRUNCATE_LEN:
                text = text[:CONTENT_TRUNCATE_LEN].rstrip() + "\n  ..."
            parts.append(text)
        elif isinstance(inner, dict):
            parts.append(str(inner))
        else:
            parts.append(repr(inner))
    return "\n".join(parts)


def extract_diff_operations(
    content_blocks: list[ToolContentBlock],
) -> tuple[FileOperation, ...]:
    """Extract file paths from diff content blocks as file operations."""
    operations: list[FileOperation] = []
    for block in content_blocks:
        if block.type != "diff":
            continue
        # v1: path field directly on the diff block
        diff_path = block.path
        if diff_path:
            old_text = block.old_text
            op = "create" if old_text is None else "modify"
            operations.append(FileOperation(op, diff_path))
        # v2: changes[] array with structured operations
        changes = getattr(block, "changes", None)
        if changes:
            for change in changes:
                operation = getattr(change, "operation", "modify")
                change_path = getattr(change, "path", None)
                if change_path:
                    operations.append(FileOperation(operation, change_path))
    return tuple(operations)


def normalize_session_update(update: SessionUpdate) -> SessionEvent | None:
    """Normalize an ACP session update into a typed event.

    Returns ``None`` for update types this client does not surface.
    """
    session_update = update.session_update

    if session_update == "agent_thought_chunk":
        thought = cast(acp_schema.AgentThoughtChunk, update)
        return ThoughtChunkEvent(thought.message_id, extract_text(update))

    if session_update == "agent_message_chunk":
        message = cast(acp_schema.AgentMessageChunk, update)
        return MessageChunkEvent(message.message_id, extract_text(update))

    if session_update == "tool_call":
        tool_call = cast(acp_schema.ToolCallStart, update)
        return ToolCallStartEvent(
            tool_call_id=tool_call.tool_call_id or "call_default",
            title=tool_call.title or "Unknown tool",
            kind=tool_call.kind,
            status=tool_call.status,
            name=tool_call.name,
            raw_input=tool_call.raw_input or None,
        )

    if session_update == "tool_call_update":
        progress = cast(acp_schema.ToolCallProgress, update)
        content = progress.content
        return ToolCallProgressEvent(
            tool_call_id=progress.tool_call_id or "call_default",
            title=progress.title,
            kind=progress.kind,
            status=progress.status,
            name=progress.name,
            content_text=(
                format_content_blocks(content, tool_kind=progress.kind)
                if content
                else None
            ),
            raw_input=progress.raw_input or None,
            raw_output=progress.raw_output or None,
            diff_operations=extract_diff_operations(content) if content else (),
        )

    if session_update == "usage_update":
        usage = cast(acp_schema.UsageUpdate, update)
        return UsageEvent(used=usage.used, size=usage.size, cost=usage.cost)

    if session_update == "current_mode_update":
        mode = cast(acp_schema.CurrentModeUpdate, update)
        return CurrentModeEvent(current_mode_id=mode.current_mode_id)

    if session_update == "config_option_update":
        config_options = cast(acp_schema.ConfigOptionUpdate, update).config_options
        model: str | None = None
        if config_options:
            for opt in config_options:
                if opt.id == "model":
                    current_value = opt.current_value
                    if current_value:
                        model = str(current_value)
                    break
        return ModelConfigEvent(model=model)

    if session_update == "plan":
        entries = cast(acp_schema.AgentPlanUpdate, update).entries or []
        return PlanEvent(entries=tuple(entries))

    if session_update == "available_commands_update":
        commands = cast(acp_schema.AvailableCommandsUpdate, update).available_commands
        return AvailableCommandsEvent(commands=list(commands or []))

    return None
