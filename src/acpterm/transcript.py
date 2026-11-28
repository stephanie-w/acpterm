"""Session transcript recorder for acpterm.

Accumulates prompt, thoughts, tool calls, file operations, permissions,
and final responses to export as formatted Markdown.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import time
from typing import Any

from .session_events import (
    MessageChunkEvent,
    PlanEvent,
    SessionEvent,
    ThoughtChunkEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
    UsageEvent,
)
from .usage import format_context_usage, format_cost, format_token_breakdown


_TERMINAL_TOOL_CALL_STATUSES = frozenset({"completed", "failed", "error", "cancelled"})


def _is_empty_raw_input(value: Any) -> bool:
    """Return whether a raw input payload carries no data."""
    return value is None or (isinstance(value, (str, list, dict)) and not value)


class TranscriptRecorder:
    """Records session events during an agent run and exports them as Markdown."""

    def __init__(self, prompt: str, resources: list[Path] | None = None) -> None:
        self.prompt = prompt
        self.resources = resources or []
        self.thoughts: list[str] = []
        self.messages: list[str] = []
        self.tool_calls: dict[str, dict[str, Any]] = {}
        self._tool_call_starts: dict[str, float] = {}
        self.plan_entries: list[Any] = []
        self.file_operations: list[dict[str, str | None]] = []
        self.permissions: list[dict[str, str]] = []
        self.usage: dict[str, Any] | None = None
        self.stop_reason: str | None = None
        self.duration_seconds: float | None = None

    def add_thought(self, text: str) -> None:
        """Add a chunk of thinking content."""
        self.thoughts.append(text)

    def add_message(self, text: str) -> None:
        """Add a chunk of message content."""
        self.messages.append(text)

    def add_tool_call(
        self,
        tool_call_id: str,
        title: str,
        kind: str,
        raw_input: Any | None = None,
        name: str | None = None,
        content: str | None = None,
    ) -> None:
        """Initialize a recorded tool call."""
        self._tool_call_starts[tool_call_id] = time.monotonic()
        self.tool_calls[tool_call_id] = {
            "name": name,
            "title": title,
            "kind": kind,
            "status": "pending",
            "content": [content] if content else [],
            "raw_input": copy.deepcopy(raw_input),
            "raw_output": None,
            "duration_seconds": None,
        }

    def update_tool_call(
        self,
        tool_call_id: str,
        status: str | None = None,
        title: str | None = None,
        content: str | None = None,
        raw_output: Any | None = None,
        raw_input: Any | None = None,
        name: str | None = None,
    ) -> None:
        """Update an existing tool call's status or append output content."""
        if tool_call_id not in self.tool_calls:
            self._tool_call_starts[tool_call_id] = time.monotonic()
            self.tool_calls[tool_call_id] = {
                "name": name,
                "title": title or "Unknown tool",
                "kind": "other",
                "status": "pending",
                "content": [],
                "raw_input": None,
                "raw_output": None,
                "duration_seconds": None,
            }
        tc = self.tool_calls[tool_call_id]
        if name is not None:
            tc["name"] = name
        if status:
            tc["status"] = status
        if title:
            tc["title"] = title
        if content:
            tc["content"].append(content)
        if raw_output is not None:
            tc["raw_output"] = copy.deepcopy(raw_output)
        if not _is_empty_raw_input(raw_input):
            tc["raw_input"] = copy.deepcopy(raw_input)
        if status in _TERMINAL_TOOL_CALL_STATUSES:
            start = self._tool_call_starts.get(tool_call_id)
            if start is None:
                start = time.monotonic()
                self._tool_call_starts[tool_call_id] = start
            tc["duration_seconds"] = round(time.monotonic() - start, 3)

    def set_usage(self, usage: dict[str, Any]) -> None:
        """Merge token usage metadata without clobbering existing values."""
        if self.usage is None:
            self.usage = dict(usage)
            return
        for key, value in usage.items():
            if value is not None or key not in self.usage:
                self.usage[key] = value

    def set_stop_reason(self, reason: str) -> None:
        """Set the turn's final stop reason."""
        self.stop_reason = reason

    def set_duration(self, seconds: float) -> None:
        """Set the turn's execution duration in seconds."""
        self.duration_seconds = seconds

    def set_plan(self, entries: list[Any]) -> None:
        """Replace the current plan entries (each update is a full snapshot)."""
        self.plan_entries = entries

    def add_file_operation(
        self,
        operation: str,
        path: str,
        *,
        line: int | None = None,
        limit: int | None = None,
    ) -> None:
        """Record a file system operation (read or write)."""
        entry: dict[str, str | None] = {"operation": operation, "path": path}
        if line is not None:
            range_str = f"L{line}"
            if limit is not None and limit > 0:
                range_str += f"-L{line + limit - 1}"
            entry["range"] = range_str
        self.file_operations.append(entry)

    def add_permission(self, title: str, kind: str, outcome: str) -> None:
        """Record a permission request and its outcome."""
        self.permissions.append({"title": title, "kind": kind, "outcome": outcome})

    def record_event(self, event: SessionEvent) -> None:
        """Record a normalized session event."""
        match event:
            case ThoughtChunkEvent():
                if event.text:
                    self.add_thought(event.text)
            case MessageChunkEvent():
                if event.text:
                    self.add_message(event.text)
            case ToolCallStartEvent():
                for operation in event.diff_operations:
                    self.add_file_operation(operation.operation, operation.path)
                self.add_tool_call(
                    event.tool_call_id,
                    event.title,
                    event.kind or "other",
                    raw_input=event.raw_input,
                    name=event.name,
                    content=event.content_text,
                )
            case ToolCallProgressEvent():
                for operation in event.diff_operations:
                    self.add_file_operation(operation.operation, operation.path)
                self.update_tool_call(
                    event.tool_call_id,
                    event.status,
                    event.title,
                    event.content_text,
                    raw_output=event.raw_output,
                    raw_input=event.raw_input,
                    name=event.name,
                )
            case UsageEvent():
                self.set_usage(
                    {"used": event.used, "size": event.size, "cost": event.cost}
                )
            case PlanEvent():
                self.set_plan(list(event.entries))

    def to_markdown(self) -> str:
        """Format the recorded session events as a clean Markdown document."""
        lines = []
        lines.append("# ACP Session Transcript")
        lines.append("")
        lines.append("## Prompt")
        lines.append(self.prompt)
        lines.append("")

        if self.resources:
            lines.append("### Attached Resources")
            for r in self.resources:
                lines.append(f"- `{r.name}` ({r})")
            lines.append("")

        if self.thoughts:
            lines.append("## Thinking")
            thought_text = "".join(self.thoughts).strip()
            if thought_text:
                lines.append("> " + thought_text.replace("\n", "\n> "))
                lines.append("")

        if self.plan_entries:
            lines.append("## Plan")
            for entry in self.plan_entries:
                content = getattr(entry, "content", str(entry))
                status = getattr(entry, "status", "pending")
                priority = getattr(entry, "priority", "medium")
                icon = {"completed": "x", "in_progress": "/"}.get(status, " ")
                priority_suffix = ""
                if priority == "high":
                    priority_suffix = " *(high)*"
                elif priority == "low":
                    priority_suffix = " *(low)*"
                lines.append(f"- [{icon}] {content}{priority_suffix}")
            lines.append("")

        if self.tool_calls:
            lines.append("## Tools Called")
            for _tc_id, tc in self.tool_calls.items():
                title = tc["title"]
                status = tc["status"]
                kind = tc["kind"]
                name = tc.get("name")
                name_suffix = f" [{name}]" if name else ""
                lines.append(f"### `{title}`{name_suffix} ({kind}) - {status}")
                if tc.get("raw_input"):
                    lines.append("**Raw Input**:")
                    lines.append("```json")
                    lines.append(json.dumps(tc["raw_input"], indent=2, default=str))
                    lines.append("```")
                if tc["content"]:
                    content_str = "\n".join(tc["content"]).strip()
                    if content_str:
                        lines.append("**Output Console**:")
                        lines.append("```")
                        lines.append(content_str)
                        lines.append("```")
                if tc.get("raw_output"):
                    lines.append("**Raw Output**:")
                    lines.append("```json")
                    lines.append(json.dumps(tc["raw_output"], indent=2, default=str))
                    lines.append("```")
                lines.append("")

        if self.file_operations:
            lines.append("## File Operations")
            for op in self.file_operations:
                operation = op["operation"]
                path = op["path"]
                range_str = op.get("range")
                suffix = f" ({range_str})" if range_str else ""
                lines.append(f"- **{operation}** `{path}`{suffix}")
            lines.append("")

        if self.permissions:
            lines.append("## Permissions")
            for perm in self.permissions:
                title = perm["title"]
                kind = perm["kind"]
                outcome = perm["outcome"]
                icon = "✅" if outcome == "approved" else "❌"
                lines.append(f"- {icon} **{title}** ({kind}) — {outcome}")
            lines.append("")

        if self.messages:
            lines.append("## Agent Response")
            lines.append("".join(self.messages).strip())
            lines.append("")

        if self.stop_reason or self.usage or self.duration_seconds is not None:
            metadata: list[str] = []
            if self.stop_reason:
                metadata.append(f"- **Stop Reason**: {self.stop_reason}")
            if self.duration_seconds is not None:
                metadata.append(f"- **Duration**: {self.duration_seconds:.2f} seconds")
            if self.usage:
                total_tokens, breakdown = format_token_breakdown(self.usage)
                if total_tokens is not None:
                    suffix = f" ({breakdown})" if breakdown else ""
                    metadata.append(
                        f"- **Token Usage**: {total_tokens:,} tokens{suffix}"
                    )
                context = format_context_usage(self.usage)
                if context is not None:
                    metadata.append(f"- **Context Usage**: {context}")
                cost = format_cost(self.usage)
                if cost is not None:
                    metadata.append(f"- **Cost**: {cost}")
            if metadata:
                lines.append("## Metadata")
                lines.extend(metadata)
                lines.append("")

        return "\n".join(lines)
