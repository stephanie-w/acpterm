#!/usr/bin/env python3
"""Permission policy script for acpterm that enforces a command whitelist.

Evaluates ACP permission requests using environment variables passed by acpterm:
- ACPTERM_PERMISSION_TITLE: Description of action (e.g. 'Run git status')
- ACPTERM_PERMISSION_KIND: Category ('execute', 'read', 'write', 'delete')
- ACPTERM_PERMISSION_PATH: Target file or directory path

Exit codes returned to acpterm:
- 0: Auto-Approve (Allowed)
- 1: Auto-Deny (Cancelled)
- 2: Fallback (Interactive terminal prompt)
"""

import os
import sys

# Whitelisted command prefixes/patterns for 'execute' operations
WHITELISTED_COMMANDS = [
    "git status",
    "git diff",
    "git log",
    "uv run pytest",
    "uv run ruff",
    "code-indexer",
    "npm test",
]


def evaluate_permission() -> int:
    title = os.environ.get("ACPTERM_PERMISSION_TITLE", "")
    kind = os.environ.get("ACPTERM_PERMISSION_KIND", "")
    path = os.environ.get("ACPTERM_PERMISSION_PATH", "")

    # 1. Always auto-approve read-only file operations
    if kind in ("read", "fs/read_file"):
        print(f"[policy:approved] Read operation auto-approved: {path or title}")
        return 0

    # 2. Check if an execution command matches the whitelist
    if kind in ("execute", "terminal/create", "unknown"):
        for cmd in WHITELISTED_COMMANDS:
            if title.startswith(f"Run {cmd}") or cmd in title:
                print(f"[policy:approved] Whitelisted command auto-approved: '{title}'")
                return 0

    # 3. Fallback for non-whitelisted actions: prompt interactively in terminal
    print(f"[policy:fallback] Action '{title}' ({kind}) requires manual approval.")
    return 2


if __name__ == "__main__":
    sys.exit(evaluate_permission())
