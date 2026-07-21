"""Session transcript recorder for acpterm.

Accumulates prompt, thoughts, tool calls, and final responses to export
as formatted Markdown.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class TranscriptRecorder:
    """Records session events during an agent run and exports them as Markdown."""

    def __init__(self, prompt: str, resources: list[Path] | None = None) -> None:
        self.prompt = prompt
        self.resources = resources or []
        self.thoughts: list[str] = []
        self.messages: list[str] = []
        self.tool_calls: dict[str, dict[str, Any]] = {}
        self.plan_entries: list[Any] = []
        self.usage: dict[str, Any] | None = None
        self.stop_reason: str | None = None

    def add_thought(self, text: str) -> None:
        """Add a chunk of thinking content."""
        self.thoughts.append(text)

    def add_message(self, text: str) -> None:
        """Add a chunk of message content."""
        self.messages.append(text)

    def add_tool_call(self, tool_call_id: str, title: str, kind: str) -> None:
        """Initialize a recorded tool call."""
        self.tool_calls[tool_call_id] = {
            "title": title,
            "kind": kind,
            "status": "pending",
            "content": [],
        }

    def update_tool_call(
        self,
        tool_call_id: str,
        status: str | None = None,
        title: str | None = None,
        content: str | None = None,
    ) -> None:
        """Update an existing tool call's status or append output content."""
        if tool_call_id not in self.tool_calls:
            self.tool_calls[tool_call_id] = {
                "title": title or "Unknown tool",
                "kind": "other",
                "status": "pending",
                "content": [],
            }
        tc = self.tool_calls[tool_call_id]
        if status:
            tc["status"] = status
        if title:
            tc["title"] = title
        if content:
            tc["content"].append(content)

    def set_usage(self, usage: dict[str, Any]) -> None:
        """Set token usage metadata."""
        self.usage = usage

    def set_stop_reason(self, reason: str) -> None:
        """Set the turn's final stop reason."""
        self.stop_reason = reason

    def set_plan(self, entries: list[Any]) -> None:
        """Replace the current plan entries (each update is a full snapshot)."""
        self.plan_entries = entries

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
            for tc_id, tc in self.tool_calls.items():
                title = tc["title"]
                status = tc["status"]
                kind = tc["kind"]
                lines.append(f"### `{title}` ({kind}) - {status}")
                if tc["content"]:
                    content_str = "\n".join(tc["content"]).strip()
                    if content_str:
                        lines.append("```")
                        lines.append(content_str)
                        lines.append("```")
                lines.append("")

        if self.messages:
            lines.append("## Agent Response")
            lines.append("".join(self.messages).strip())
            lines.append("")

        if self.stop_reason:
            lines.append("## Metadata")
            lines.append(f"- **Stop Reason**: {self.stop_reason}")
            if self.usage:
                used = self.usage.get("used")
                size = self.usage.get("size")
                cost = self.usage.get("cost")
                if used is not None and size is not None:
                    pct = (used / size * 100) if size > 0 else 0
                    lines.append(
                        f"- **Context Usage**: {used:,} / {size:,} tokens ({pct:.1f}%)"
                    )
                if cost is not None:
                    amount = getattr(cost, "amount", None)
                    currency = getattr(cost, "currency", None)
                    if amount is not None and currency:
                        lines.append(f"- **Cost**: ${amount:.2f} {currency}")
            lines.append("")

        return "\n".join(lines)
