from __future__ import annotations

from typing import Any

from acp import schema as acp_schema
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape

from .session_events import (
    CONTENT_TRUNCATE_LEN,
    CurrentModeEvent,
    extract_text as _extract_text,
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
    ToolContentBlock,
    UsageEvent,
)
from .usage import format_context_usage, format_cost, format_token_breakdown


_console = Console(highlight=False)

# Public surface retained for compatibility with existing importers.
__all__ = [
    "CONTENT_TRUNCATE_LEN",
    "SessionResponse",
    "SessionUpdate",
    "ToolContentBlock",
    "_extract_text",
    "_format_content_blocks",
    "display_initial_session_info",
    "flush_output",
    "format_session_update",
    "format_stop_reason",
    "render_session_event",
    "reset_state",
]

SessionResponse = (
    acp_schema.NewSessionResponse
    | acp_schema.LoadSessionResponse
    | acp_schema.ResumeSessionResponse
)

_PLAN_STATUS_ICONS: dict[str, str] = {
    "pending": "○",
    "in_progress": "▸",
    "completed": "✓",
}

_PLAN_STATUS_STYLES: dict[str, str] = {
    "pending": "dim",
    "in_progress": "yellow",
    "completed": "green",
}


def _tag(label: str, style: str | None = None) -> str:
    escaped = f"\\[{escape(label)}]"
    if style:
        return f"[{style}]{escaped}[/{style}]"
    return escaped


class _State:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.msg_id: str | None = None
        self.update_type: str | None = None
        self.in_stream: bool = False
        self.tool_kind: str | None = None
        self._md_buffer: dict[str, list[str]] = {}
        self._last_md_msg_id: str | None = None
        self._usage: dict[str, Any] | None = None
        self._displayed_model: str | None = None
        self._displayed_mode: str | None = None

    def end_stream(self) -> None:
        if self.in_stream:
            _console.print()
            self.in_stream = False
        self.update_type = None
        self.msg_id = None

    def begin_stream(self, msg_id: str | None) -> bool:
        if (
            self.in_stream
            and self.msg_id == msg_id
            and self.update_type == "agent_thought_chunk"
        ):
            return True
        self.end_stream()
        return False

    def flush_md(self) -> None:
        if not self._md_buffer:
            return
        for parts in self._md_buffer.values():
            text = "".join(parts)
            if text:
                _console.print(Markdown(text))
        self._md_buffer.clear()
        self._last_md_msg_id = None

    def buffer_md(self, msg_id: str | None, text: str) -> None:
        if self._last_md_msg_id is not None and self._last_md_msg_id != msg_id:
            self.flush_md()
        key = msg_id or "_default"
        self._md_buffer.setdefault(key, []).append(text)
        self._last_md_msg_id = msg_id


_state = _State()


def reset_state() -> None:
    """Reset all streaming display state for a new agent run."""
    _state.reset()


def flush_output() -> None:
    """End any active inline stream and render buffered Markdown."""
    _state.end_stream()
    _state.flush_md()


def _format_content_blocks(content_blocks: list[ToolContentBlock]) -> str:
    """Render tool content blocks using the current terminal tool kind.

    Kept for compatibility; new code should call
    :func:`session_events.format_content_blocks` with an explicit ``tool_kind``.
    """
    return format_content_blocks(content_blocks, tool_kind=_state.tool_kind)


def render_session_event(event: SessionEvent) -> None:
    """Render a normalized session event to the terminal."""
    match event:
        case ThoughtChunkEvent():
            if not event.text:
                return
            _state.flush_md()
            if _state.begin_stream(event.message_id):
                _console.print(event.text, end="", markup=False)
                return
            _console.print(f"{_tag('thinking', 'dim cyan')} ", end="")
            _console.print(event.text, end="", markup=False)
            _state.in_stream = True
            _state.update_type = "agent_thought_chunk"
            _state.msg_id = event.message_id

        case MessageChunkEvent():
            _state.end_stream()
            if event.text:
                _state.buffer_md(event.message_id, event.text)

        case ToolCallStartEvent():
            _state.end_stream()
            _state.flush_md()
            _state.tool_kind = event.kind
            status = event.status or "pending"
            name_suffix = f" {_tag(event.name)}" if event.name else ""
            _console.print(
                f"{_tag('tool' if _state.tool_kind is None else _state.tool_kind, 'yellow')} {escape(event.title)}{name_suffix} ({status})"
            )

        case ToolCallProgressEvent():
            _state.end_stream()
            _state.flush_md()
            _state.tool_kind = event.kind

            parts: list[str] = []
            if event.title:
                parts.append(escape(event.title))
            if event.name:
                parts.append(_tag(event.name))
            if event.status:
                parts.append(f"({event.status})")
            header = " ".join(parts)

            tag_label = _state.tool_kind if _state.tool_kind else "tool"
            if header:
                _console.print(f"{_tag(tag_label, 'yellow')} {header}")
            if event.content_text:
                indented = "\n".join(
                    f"  {line}" for line in event.content_text.splitlines()
                )
                _console.print(indented, markup=False)

        case UsageEvent():
            _state._usage = {
                "used": event.used,
                "size": event.size,
                "cost": event.cost,
            }

        case CurrentModeEvent():
            mode = event.current_mode_id
            if mode and mode != _state._displayed_mode:
                flush_output()
                _state._displayed_mode = mode
                _console.print(f"{_tag('mode', 'dim')} {escape(mode)}")

        case ModelConfigEvent():
            model = event.model
            if model and model != _state._displayed_model:
                flush_output()
                _state._displayed_model = model
                _console.print(f"{_tag('model', 'dim')} {escape(model)}")

        case PlanEvent():
            _state.end_stream()
            _state.flush_md()
            if event.entries:
                _console.print(f"\n{_tag('plan', 'bold cyan')}")
                for entry in event.entries:
                    content = entry.content
                    priority = entry.priority
                    status = entry.status

                    icon = _PLAN_STATUS_ICONS.get(status, "○")
                    style = _PLAN_STATUS_STYLES.get(status, "")

                    priority_tag = ""
                    if priority == "high":
                        priority_tag = " [red]↑[/red]"
                    elif priority == "low":
                        priority_tag = " [dim]↓[/dim]"

                    icon_str = f"[{style}]{icon}[/{style}]" if style else icon
                    _console.print(f"  {icon_str} {escape(content)}{priority_tag}")


def format_session_update(
    session_id: str,
    update: SessionUpdate,
) -> None:
    """Normalize and render an ACP session update."""
    event = normalize_session_update(update)
    if event is not None:
        render_session_event(event)


def display_initial_session_info(session_response: SessionResponse) -> None:
    """Print initial model and mode information from the session load/new response."""
    # Print mode if present
    current_mode_id = (
        session_response.modes.current_mode_id if session_response.modes else None
    )
    if current_mode_id:
        render_session_event(CurrentModeEvent(current_mode_id=current_mode_id))

    # Print model if present in config_options
    model_from_config = False
    config_options = session_response.config_options
    if config_options:
        for opt in config_options:
            if opt.id == "model":
                model = opt.current_value
                if model:
                    model_from_config = True
                    render_session_event(ModelConfigEvent(model=str(model)))
                break

    # Support metadata fallback for opencode
    meta = session_response.field_meta
    if not model_from_config and meta and isinstance(meta, dict):
        opencode = meta.get("opencode")
        if isinstance(opencode, dict):
            model = opencode.get("modelId") or opencode.get("model_id")
            if model:
                render_session_event(ModelConfigEvent(model=str(model)))


def format_stop_reason(stop_reason: str, usage: dict[str, Any] | None = None) -> None:
    flush_output()
    _print_usage(usage=usage)
    _console.print(f"{_tag('done', 'dim')} {stop_reason}")


def _print_usage(usage: dict[str, Any] | None = None) -> None:
    session_usage = _state._usage
    _state._usage = None

    merged: dict[str, Any] = dict(session_usage or {})
    if usage:
        merged.update(usage)

    total_tokens, breakdown = format_token_breakdown(merged)

    lines: list[str] = []
    if total_tokens is not None:
        suffix = f" ({breakdown})" if breakdown else ""
        lines.append(f"{_tag('tokens', 'dim')} {total_tokens:,} tokens{suffix}")

    context = format_context_usage(merged)
    if context is not None:
        lines.append(f"{_tag('context', 'dim')} {context}")
    cost = format_cost(merged)
    if cost is not None:
        lines.append(f"{_tag('cost', 'dim')} {cost}")

    if lines:
        _console.print()
        for line in lines:
            _console.print(line)
