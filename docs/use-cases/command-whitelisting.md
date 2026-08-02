# Command Whitelisting & Permission Policies

This guide explains how to define auto-approved command whitelists and custom security policies in `acpterm` using **Permission Policy Hooks** (`-p / --on-permission`).

---

## 🛠️ Overview of Permission Policies

When an ACP agent requests permission to run a terminal command or manipulate files, `acpterm` evaluates matching permission hooks and passes event details as environment variables:

* `ACPTERM_PERMISSION_TITLE`: Description of the action (e.g. `Run git status`, `Run npm test`).
* `ACPTERM_PERMISSION_KIND`: Operation category (`execute`, `read`, `write`, `delete`).
* `ACPTERM_PERMISSION_PATH`: Target resource path.

### Policy Script Exit Codes

Your policy script communicates its decision to `acpterm` via exit codes:

| Exit Code | Action | Result |
| :---: | :--- | :--- |
| **`0`** | **Auto-Approve** | Instantly allows the action without prompting. |
| **`1`** | **Auto-Deny** | Instantly cancels the action and notifies the agent. |
| **`2`** | **Fallback** | Prompts the user interactively in the terminal (`Allow? [y/N]`). |

---

## 🚀 Setting Up `scripts/command_whitelist.py`

Create or customize `scripts/command_whitelist.py` to specify your whitelisted commands:

```python
#!/usr/bin/env python3
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

    # 1. Always auto-approve read-only file operations
    if kind in ("read", "fs/read_file"):
        return 0  # Auto-Approve

    # 2. Check if execution command matches the whitelist
    if kind in ("execute", "terminal/create", "unknown"):
        for cmd in WHITELISTED_COMMANDS:
            if title.startswith(f"Run {cmd}") or cmd in title:
                return 0  # Auto-Approve

    # 3. Fallback for non-whitelisted actions to interactive prompt
    return 2  # Fallback to interactive terminal prompt

if __name__ == "__main__":
    sys.exit(evaluate_permission())
```

---

## 💻 CLI Usage

Pass the whitelist script via `-p` or `--on-permission`:

```bash
acpterm -a opencode prompt "Check git status and run pytest" -p "python3 scripts/command_whitelist.py"
```

---

## 🌐 Global Configuration (`~/.acpterm/config.json`)

To enforce your command whitelist automatically across all `acpterm` sessions, add the policy to your global configuration:

```json
{
  "hooks": [
    {
      "name": "command-whitelist-policy",
      "on": "permission",
      "run": "python3 /absolute/path/to/scripts/command_whitelist.py"
    }
  ]
}
```
