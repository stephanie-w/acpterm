#!/usr/bin/env python3
"""Audit script for evaluating MCP tool usage from an exported acpterm transcript.

Reads environment variables populated by acpterm:
- ACPTERM_TRANSCRIPT: Path to the exported Markdown transcript.
- ACPTERM_AGENT: Agent name.
- ACPTERM_DURATION_SECONDS: Execution duration.
"""

from pathlib import Path
import os
import re
import sys


def audit_transcript() -> None:
    transcript_str = os.environ.get("ACPTERM_TRANSCRIPT", "")
    if not transcript_str:
        print("[audit:skip] No ACPTERM_TRANSCRIPT environment variable provided.")
        sys.exit(0)

    transcript_path = Path(transcript_str)
    if not transcript_path.exists():
        print(f"[audit:error] Transcript file not found at: {transcript_path}")
        sys.exit(1)

    content = transcript_path.read_text(encoding="utf-8")
    agent_name = os.environ.get("ACPTERM_AGENT", "unknown_agent")
    duration = os.environ.get("ACPTERM_DURATION_SECONDS", "N/A")

    # MCP tool patterns to track
    mcp_tools = [
        "list_codebases",
        "search_symbols",
        "get_symbol_source",
        "get_codebase_stats",
    ]

    counts = {tool: len(re.findall(rf"\b{tool}\b", content)) for tool in mcp_tools}
    total_calls = sum(counts.values())

    print("=" * 50)
    print(f"📊 MCP Usage Audit Report for Agent [{agent_name}]")
    print(f"⏱️ Turn Duration: {duration}s")
    print("-" * 50)

    for tool, count in counts.items():
        icon = "✅" if count > 0 else "⚪"
        print(f"{icon} Tool '{tool}': {count} call(s)")

    print("-" * 50)
    
    # Check for potential anti-patterns
    issues = []
    
    # Check 1: No MCP tools used at all
    if total_calls == 0:
        issues.append("WARNING: No MCP tools were invoked during this turn!")

    # Check 2: Unscoped search_symbols (missing codebase_id parameter check)
    search_blocks = re.findall(r"code-indexer_search_symbols.*?```json\n(.*?)\n```", content, re.DOTALL)
    for block in search_blocks:
        if "codebase_id" not in block:
            issues.append("WARNING: search_symbols was invoked without codebase_id scoping.")

    if issues:
        print("⚠️ Anti-Patterns & Warnings Detected:")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print("✨ No major anti-patterns detected in MCP tool calls.")

    print("=" * 50)


if __name__ == "__main__":
    audit_transcript()
