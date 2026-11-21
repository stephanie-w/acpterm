from __future__ import annotations

from typing import Any, cast

from acp import schema as acp_schema
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape

from .usage import format_context_usage, format_cost, format_token_breakdown


_console = Console(highlight=False)

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

SessionResponse = (
    acp_schema.NewSessionResponse
    | acp_schema.LoadSessionResponse
    | acp_schema.ResumeSessionResponse
)

ToolContentBlock = (
    acp_schema.ContentToolCallContent
    | acp_schema.FileEditToolCallContent
    | acp_schema.TerminalToolCallContent
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
        self.msg_id: str | None = None
        self.update_type: str | None = None
        self.in_stream: bool = False
        self.tool_kind: str | None = None
        self._md_buffer: dict[str, list[str]] = {}
        self._last_md_msg_id: str | None = None
        self._usage: dict[str, Any] | None = None
        self._model_displayed: bool = False
        self._mode_displayed: bool = False

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


def _extract_text(content: Any) -> str:
    if isinstance(content, acp_schema.TextContentBlock):
        return content.text or ""
    if hasattr(content, "content"):
        return _extract_text(content.content)
    text_value = getattr(content, "text", None)
    if text_value is not None:
        return str(text_value)
    return ""


def _format_content_blocks(content_blocks: list[ToolContentBlock]) -> str:
    parts: list[str] = []
    for block in content_blocks:
        # Handle terminal content references (type="terminal", terminal_id=...)
        if block.type == "terminal":
            terminal_id = block.terminal_id
            parts.append(_tag(f"terminal {terminal_id or 'unknown'}"))
            continue

        if hasattr(block, "content"):
            inner = block.content
        else:
            inner = block
        text = _extract_text(inner)
        if text:
            if _state.tool_kind == "read" and len(text) > CONTENT_TRUNCATE_LEN:
                text = text[:CONTENT_TRUNCATE_LEN].rstrip() + "\n  ..."
            parts.append(text)
        elif isinstance(inner, dict):
            parts.append(str(inner))
        else:
            parts.append(repr(inner))
    return "\n".join(parts)


def format_session_update(
    session_id: str,
    update: SessionUpdate,
) -> None:
    session_update = update.session_update

    if session_update == "agent_thought_chunk":
        msg_id = cast(acp_schema.AgentThoughtChunk, update).message_id
        text = _extract_text(update)
        if not text:
            return
        if _state.begin_stream(msg_id):
            _console.print(text, end="")
            return
        _console.print(f"{_tag('thinking', 'dim cyan')} ", end="")
        _console.print(text, end="")
        _state.in_stream = True
        _state.update_type = "agent_thought_chunk"
        _state.msg_id = msg_id

    elif session_update == "agent_message_chunk":
        _state.end_stream()
        msg_id = cast(acp_schema.AgentMessageChunk, update).message_id
        text = _extract_text(update)
        if text:
            _state.buffer_md(msg_id, text)

    elif session_update == "tool_call":
        _state.end_stream()
        _state.flush_md()
        tool_call = cast(acp_schema.ToolCallStart, update)
        title = tool_call.title or "Unknown tool"
        name = tool_call.name
        kind = tool_call.kind
        _state.tool_kind = kind
        status = tool_call.status or "pending"
        name_suffix = f" {_tag(name)}" if name else ""
        _console.print(
            f"{_tag('tool' if _state.tool_kind is None else _state.tool_kind, 'yellow')} {escape(title)}{name_suffix} ({status})"
        )

    elif session_update == "tool_call_update":
        _state.end_stream()
        _state.flush_md()
        tool_call = cast(acp_schema.ToolCallProgress, update)
        status = tool_call.status
        title = tool_call.title
        name = tool_call.name
        kind = tool_call.kind
        _state.tool_kind = kind
        content = tool_call.content

        parts: list[str] = []
        if title:
            parts.append(escape(title))
        if name:
            parts.append(_tag(name))
        if status:
            parts.append(f"({status})")
        header = " ".join(parts)

        tag_label = _state.tool_kind if _state.tool_kind else "tool"
        if header:
            _console.print(f"{_tag(tag_label, 'yellow')} {header}")
        if content:
            text = _format_content_blocks(content)
            if text:
                indented = "\n".join(f"  {line}" for line in text.splitlines())
                _console.print(indented)

    elif session_update == "usage_update":
        usage = cast(acp_schema.UsageUpdate, update)
        used = usage.used
        size = usage.size
        cost = usage.cost
        _state._usage = {"used": used, "size": size, "cost": cost}

    elif session_update == "current_mode_update":
        current_mode_id = cast(acp_schema.CurrentModeUpdate, update).current_mode_id
        if current_mode_id and not _state._mode_displayed:
            _state._mode_displayed = True
            _console.print(f"{_tag('mode', 'dim')} {current_mode_id}")

    elif session_update == "config_option_update":
        if not _state._model_displayed:
            config_options = cast(acp_schema.ConfigOptionUpdate, update).config_options
            if config_options:
                for opt in config_options:
                    if opt.id == "model":
                        model = opt.current_value
                        if model:
                            _state._model_displayed = True
                            _console.print(f"{_tag('model', 'dim')} {model}")
                        break

    elif session_update == "plan":
        _state.end_stream()
        _state.flush_md()
        entries = cast(acp_schema.AgentPlanUpdate, update).entries or []
        if entries:
            _console.print(f"\n{_tag('plan', 'bold cyan')}")
            for entry in entries:
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

                _console.print(f"  [{style}]{icon}[/{style}] {content}{priority_tag}")


def display_initial_session_info(session_response: SessionResponse) -> None:
    """Print initial model and mode information from the session load/new response."""
    # Print mode if present
    current_mode_id = (
        session_response.modes.current_mode_id if session_response.modes else None
    )
    if current_mode_id and not _state._mode_displayed:
        _state._mode_displayed = True
        _console.print(f"{_tag('mode', 'dim')} {current_mode_id}")

    # Print model if present in config_options
    config_options = session_response.config_options
    if config_options:
        for opt in config_options:
            if opt.id == "model":
                model = opt.current_value
                if model and not _state._model_displayed:
                    _state._model_displayed = True
                    _console.print(f"{_tag('model', 'dim')} {model}")
                break

    # Support metadata fallback for opencode
    meta = session_response.field_meta
    if meta and isinstance(meta, dict):
        opencode = meta.get("opencode")
        if isinstance(opencode, dict):
            model = opencode.get("modelId") or opencode.get("model_id")
            if model and not _state._model_displayed:
                _state._model_displayed = True
                _console.print(f"{_tag('model', 'dim')} {model}")


def format_stop_reason(stop_reason: str, usage: dict[str, Any] | None = None) -> None:
    _state.end_stream()
    _state.flush_md()
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
