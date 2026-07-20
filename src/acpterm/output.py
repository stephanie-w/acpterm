from __future__ import annotations

from typing import Any

from acp import schema as acp_schema
from rich.console import Console
from rich.markdown import Markdown

_console = Console(highlight=False)

CONTENT_TRUNCATE_LEN = 300


def _tag(label: str, style: str | None = None) -> str:
    escaped = f"\\[{label}]"
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


def _format_content_blocks(content_blocks: list[Any]) -> str:
    parts: list[str] = []
    for block in content_blocks:
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
    update: Any,
) -> None:
    session_update = getattr(update, "session_update", None) or getattr(
        update, "sessionUpdate", None
    )

    if session_update == "agent_thought_chunk":
        msg_id: str | None = getattr(update, "message_id", None) or getattr(
            update, "messageId", None
        )
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
        msg_id: str | None = getattr(update, "message_id", None) or getattr(
            update, "messageId", None
        )
        text = _extract_text(update)
        if text:
            _state.buffer_md(msg_id, text)

    elif session_update == "tool_call":
        _state.end_stream()
        _state.flush_md()
        title = getattr(update, "title", "Unknown tool") or "Unknown tool"
        kind = getattr(update, "kind", None)
        if kind is not None:
            _state.tool_kind = kind.value if hasattr(kind, "value") else str(kind)
        else:
            _state.tool_kind = None
        status = getattr(update, "status", "pending") or "pending"
        _console.print(
            f"{_tag('tool' if _state.tool_kind is None else _state.tool_kind, 'yellow')} {title} ({status})"
        )

    elif session_update == "tool_call_update":
        _state.end_stream()
        _state.flush_md()
        status = getattr(update, "status", None)
        title = getattr(update, "title", None)
        kind = getattr(update, "kind", None)
        if kind is not None:
            _state.tool_kind = kind.value if hasattr(kind, "value") else str(kind)
        content = getattr(update, "content", None)

        parts: list[str] = []
        if title:
            parts.append(title)
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
        used = getattr(update, "used", None)
        size = getattr(update, "size", None)
        cost = getattr(update, "cost", None)
        _state._usage = {"used": used, "size": size, "cost": cost}

    elif session_update == "current_mode_update":
        current_mode_id = getattr(update, "current_mode_id", None) or getattr(
            update, "currentModeId", None
        )
        if current_mode_id and not _state._mode_displayed:
            _state._mode_displayed = True
            _console.print(f"{_tag('mode', 'dim')} {current_mode_id}")

    elif session_update == "config_option_update":
        if not _state._model_displayed:
            config_options = getattr(update, "config_options", None) or getattr(
                update, "configOptions", None
            )
            if config_options:
                for opt in config_options:
                    if getattr(opt, "id", None) == "model":
                        model = getattr(opt, "current_value", None) or getattr(
                            opt, "currentValue", None
                        )
                        if model:
                            _state._model_displayed = True
                            _console.print(f"{_tag('model', 'dim')} {model}")
                        break


def format_stop_reason(stop_reason: str) -> None:
    _state.end_stream()
    _state.flush_md()
    _print_usage()
    _console.print(f"{_tag('done', 'dim')} {stop_reason}")


def _print_usage() -> None:
    if _state._usage is None:
        return
    usage = _state._usage
    used = usage.get("used")
    size = usage.get("size")
    cost = usage.get("cost")

    lines: list[str] = []
    if used is not None and size is not None:
        pct = (used / size * 100) if size > 0 else 0
        lines.append(
            f"{_tag('context', 'dim')} {used:,} / {size:,} tokens ({pct:.1f}%)"
        )
    if cost is not None:
        amount = getattr(cost, "amount", None)
        currency = getattr(cost, "currency", None)
        if amount is not None and currency:
            lines.append(f"{_tag('cost', 'dim')} ${amount:.2f} {currency}")

    if lines:
        _console.print()
        for line in lines:
            _console.print(line)
