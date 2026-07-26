#!/usr/bin/env python3
"""Slow Response & Performance Monitor Hook for acpterm.

Analyzes turn duration, context usage, and tool performance.
Logs performance metrics to ~/.acpterm/perf_log.json and prints a warning
alert if the agent turn exceeds a configured duration threshold.

Usage as a hook:
  acpterm exec "..." --on-turn-end "python3 scripts/monitor_slow_response.py"

Or in ~/.acpterm/config.json:
  {
    "hooks": [
      {
        "name": "perf-monitor",
        "on": "turn_end",
        "run": "python3 scripts/monitor_slow_response.py"
      }
    ]
  }
"""

from __future__ import annotations

from datetime import datetime, timezone, UTC
import json
import os
from pathlib import Path
import sys

from rich.console import Console
from rich.panel import Panel


_console = Console(highlight=False)

# Configurable threshold via env variable (default 10 seconds)
DEFAULT_THRESHOLD = 10.0


def parse_transcript_metadata(transcript_path: Path) -> dict[str, str]:
    """Parse context usage and cost from transcript markdown if available."""
    metadata = {}
    if not transcript_path.exists():
        return metadata

    text = transcript_path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if "**Context Usage**:" in line:
            metadata["context_usage"] = line.split("**Context Usage**:", 1)[1].strip()
        elif "**Cost**:" in line:
            metadata["cost"] = line.split("**Cost**:", 1)[1].strip()
        elif "### `" in line and "(" in line and ")" in line:
            # Count tool calls
            metadata["tool_calls"] = str(int(metadata.get("tool_calls", 0)) + 1)
    return metadata


def main() -> None:
    duration_str = os.environ.get("ACPTERM_DURATION_SECONDS", "")
    if not duration_str:
        sys.exit(0)

    try:
        duration = float(duration_str)
    except ValueError:
        sys.exit(0)

    agent = os.environ.get("ACPTERM_AGENT", "unknown")
    session_id = os.environ.get("ACPTERM_SESSION_ID", "—")
    stop_reason = os.environ.get("ACPTERM_STOP_REASON", "—")
    prompt = os.environ.get("ACPTERM_PROMPT", "—")
    transcript_str = os.environ.get("ACPTERM_TRANSCRIPT", "")

    threshold = float(os.environ.get("SLOW_THRESHOLD_SECONDS", DEFAULT_THRESHOLD))

    transcript_path = Path(transcript_str) if transcript_str else None
    metadata = parse_transcript_metadata(transcript_path) if transcript_path else {}

    # Prepare performance log entry
    log_entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "agent": agent,
        "session_id": session_id,
        "duration_seconds": duration,
        "threshold_seconds": threshold,
        "is_slow": duration >= threshold,
        "stop_reason": stop_reason,
        "prompt": prompt[:120] + ("..." if len(prompt) > 120 else ""),
        "context_usage": metadata.get("context_usage"),
        "cost": metadata.get("cost"),
        "tool_calls_count": int(metadata.get("tool_calls", 0)),
    }

    # Save to ~/.acpterm/perf_log.json
    log_file = Path.home() / ".acpterm" / "perf_log.json"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    history = []
    if log_file.exists():
        try:
            history = json.loads(log_file.read_text(encoding="utf-8"))
        except Exception:
            history = []

    history.append(log_entry)
    # Keep last 100 entries
    history = history[-100:]
    log_file.write_text(json.dumps(history, indent=2), encoding="utf-8")

    # If response was slow, display alert
    if duration >= threshold:
        lines = [
            f"[bold yellow]Agent:[/] {agent}  |  [bold yellow]Duration:[/] {duration:.2f}s (Threshold: {threshold:.2f}s)",
            f"[bold yellow]Session:[/] {session_id}  |  [bold yellow]Stop Reason:[/] {stop_reason}",
        ]
        if metadata.get("context_usage"):
            lines.append(f"[bold yellow]Context:[/] {metadata['context_usage']}")
        if metadata.get("cost"):
            lines.append(f"[bold yellow]Cost:[/] {metadata['cost']}")
        if metadata.get("tool_calls"):
            lines.append(f"[bold yellow]Tools Executed:[/] {metadata['tool_calls']}")

        lines.append(f"[dim]Prompt: {prompt[:80]}...[/dim]")

        _console.print()
        _console.print(
            Panel(
                "\n".join(lines),
                title="⚠️ [bold red]SLOW RESPONSE DETECTED[/bold red]",
                border_style="yellow",
            )
        )


if __name__ == "__main__":
    main()
