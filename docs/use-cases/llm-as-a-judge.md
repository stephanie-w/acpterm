# LLM-as-a-Judge & Agent Usage Auditing Guide

This guide details how to use `acpterm` to evaluate agent performance, code quality, and skill/MCP adoption using standard LLM-as-a-Judge evaluation techniques and automated lifecycle hooks.

---

## Overview of Evaluation Patterns

`acpterm` supports four distinct approaches to monitoring, auditing, and judging agent execution:

| Pattern | Mechanism | Best Used For |
| :--- | :--- | :--- |
| **Pattern 1: Interactive 2-Step Judging** | `acpterm --export` + `-f skills/...` | In-depth LLM evaluations against quality rules or skill criteria. |
| **Pattern 2: Automated Post-Turn Auditing** | `-k / --on-turn-end` + Python script | Lightweight, programmatic turn auditing & metrics collection. |
| **Pattern 3: Global Configuration Hooks** | `~/.acpterm/config.json` | Continuous automated auditing across all terminal sessions. |
| **Pattern 4: Permission & Policy Interception** | `-p / --on-permission` | Real-time security auditing and auto-approval rules. |

---

## Pattern 1: Interactive 2-Step LLM-as-a-Judge

In this pattern, you execute a task with `--export transcript.md` to produce a ground-truth Markdown log of the turn (including prompts, raw tool inputs/outputs, file operations, and metadata). Then, you feed the transcript into a second agent turn using an evaluation skill file.

### Example A: Code Quality & Anti-Pattern Evaluation

Evaluate python code quality against project rules using `skills/llm_judge/SKILL.md`:

```bash
# Step 1: Run target task and export transcript
acpterm -a opencode exec "refactor the authentication module" --export transcript.md

# Step 2: Run judge evaluation using skill instructions
acpterm -a opencode exec -f skills/llm_judge/SKILL.md "Evaluate the transcript in transcript.md against code quality guidelines."
```

### Example B: MCP Skill Adoption & Parameter Scoping Evaluation

Evaluate whether an agent used an MCP server (such as `code-indexer`) properly using `skills/mcp_usage_judge/SKILL.md`:

```bash
# Step 1: Run task requiring symbol retrieval and export transcript
acpterm -a opencode exec "Find CodeIndexer definition in code-indexer" --export transcript.md

# Step 2: Run judge evaluation using MCP skill rules
acpterm -a opencode exec -f skills/mcp_usage_judge/SKILL.md "Evaluate the MCP skill usage in transcript.md."
```

---

## Pattern 2: Automated Post-Turn Auditing Scripts (`-k / --on-turn-end`)

You can run an automated Python audit script whenever an agent turn ends. `acpterm` populates turn context into environment variables:
* `ACPTERM_TRANSCRIPT`: Path to the exported Markdown transcript.
* `ACPTERM_AGENT`: Name of the active agent (`opencode`, `kiro`, `agy`).
* `ACPTERM_DURATION_SECONDS`: Total execution duration in seconds.
* `ACPTERM_PROMPT`: The original user prompt.

### Example Audit Script (`scripts/audit_mcp_usage.py`)

```python
#!/usr/bin/env python3
from pathlib import Path
import os
import re
import sys

def audit():
    transcript_str = os.environ.get("ACPTERM_TRANSCRIPT", "")
    if not transcript_str or not Path(transcript_str).exists():
        print("[audit:skip] No transcript available.")
        sys.exit(0)

    content = Path(transcript_str).read_text(encoding="utf-8")
    agent = os.environ.get("ACPTERM_AGENT", "opencode")
    duration = os.environ.get("ACPTERM_DURATION_SECONDS", "N/A")

    # Audit MCP tool calls
    tools = ["list_codebases", "search_symbols", "get_symbol_source"]
    counts = {t: len(re.findall(rf"\b{t}\b", content)) for t in tools}

    print(f"📊 Audit for [{agent}] ({duration}s): {counts}")

if __name__ == "__main__":
    audit()
```

### Running with `-k / --on-turn-end`

```bash
acpterm -a opencode prompt "Find definition of run_permission_hook" \
  --export transcript.md \
  -k "python3 scripts/audit_mcp_usage.py"
```

---

## Pattern 3: Global Configuration Hooks (`~/.acpterm/config.json`)

To make post-turn auditing automatic across all CLI sessions without having to pass `-k` every time, configure permanent hooks in `~/.acpterm/config.json`:

```json
{
  "hooks": [
    {
      "name": "auto-mcp-audit",
      "on": "turn_end",
      "run": "python3 /path/to/acpterm/scripts/audit_mcp_usage.py"
    }
  ]
}
```

Whenever any agent turn completes, `acpterm` will run the audit script automatically.

---

## Pattern 4: Real-Time Permission Policy Interception (`-p / --on-permission`)

When an agent requests permission to run shell commands or modify files, `acpterm` can delegate the decision to a permission policy hook script using `-p` or `--on-permission`.

### Environment Variables Provided to Permission Hooks:
* `ACPTERM_PERMISSION_TITLE`: Summary of the requested action (e.g. `Write text file src/auth.py`).
* `ACPTERM_PERMISSION_KIND`: Category (`read`, `write`, `execute`, `delete`).
* `ACPTERM_PERMISSION_PATH`: Target resource path.

### Example Permission Policy Script (`scripts/permission_policy.py`)

```python
#!/usr/bin/env python3
import os
import sys

title = os.environ.get("ACPTERM_PERMISSION_TITLE", "")
kind = os.environ.get("ACPTERM_PERMISSION_KIND", "")
path = os.environ.get("ACPTERM_PERMISSION_PATH", "")

# Log all permission requests for audit trail
with open("permissions.log", "a") as f:
    f.write(f"[{kind}] {title} (path: {path})\n")

# Auto-approve read operations, trigger fallback for others
if kind == "read":
    sys.exit(0)  # Exit code 0: Auto-Approve

sys.exit(2)  # Exit code 2: Fallback to interactive prompt
```

### Running with `-p / --on-permission`

```bash
acpterm -a opencode prompt "refactor tests" -p "python3 scripts/permission_policy.py"
```
