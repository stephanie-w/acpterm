#!/usr/bin/env python3
"""Dynamic Permission Policy Engine Hook for acpterm.

Evaluates permission requests dynamically based on operation kind and target path:
  Exit Code 0: Auto-Approve (Allowed)
  Exit Code 1: Auto-Deny (Cancelled)
  Exit Code 2: Fallback (Prompt human for approval)

Usage as a hook:
  acpterm exec "..." -p "python3 scripts/permission_policy.py"
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
import sys


# High-risk file patterns to ALWAYS deny
DENY_PATTERNS = [".env", ".git", "id_rsa", "secrets"]

# Safe directories where file modifications are auto-approved
ALLOWED_DIRS = ["src", "tests", "demos", "scripts", "docs"]


def main() -> None:
    kind = os.environ.get("ACPTERM_PERMISSION_KIND", "").lower()
    os.environ.get("ACPTERM_PERMISSION_TITLE", "")
    target_path_str = os.environ.get("ACPTERM_PERMISSION_PATH", "")
    cwd_str = os.environ.get("ACPTERM_CWD", str(Path.cwd()))

    cwd = Path(cwd_str).resolve()

    # Always approve read-only permissions
    if kind == "read":
        sys.exit(0)

    # Check for target path
    if target_path_str:
        path = Path(target_path_str)

        # 1. Deny high-risk filenames or secrets
        for pattern in DENY_PATTERNS:
            if pattern in path.name.lower() or pattern in str(path).lower():
                print(f"[Policy] Denied sensitive path: {path}", file=sys.stderr)
                sys.exit(1)

        # 2. Resolve path against CWD
        with contextlib.suppress(Exception):
            resolved = path.resolve()
            # Prevent path traversal outside project CWD
            if not resolved.is_relative_to(cwd):
                print(
                    f"[Policy] Denied path outside project CWD: {path}", file=sys.stderr
                )
                sys.exit(1)

            # Auto-approve if inside an allowed directory
            rel_parts = resolved.relative_to(cwd).parts
            if rel_parts and rel_parts[0] in ALLOWED_DIRS:
                sys.exit(0)

    # 3. Fallback to terminal prompt for unknown files/commands
    sys.exit(2)


if __name__ == "__main__":
    main()
