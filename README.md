# acpterm

A CLI client for the [Agent Client Protocol (ACP)][acp]. Spawns an ACP-compatible agent
and displays its thinking, tool calls, and responses in the terminal.

## Install

```bash
uv sync
```

## Quick Start

One-shot prompt (no session saved):

```bash
acpterm -a opencode exec "what does this repo do?"
```

Create a named session and send prompts:

```bash
acpterm -a opencode prompt -s my-session "find the flaky test and fix it"
```

Send follow-up prompts in the same session, or use `-r` to resume the last session:

```bash
acpterm -a opencode prompt -s my-session "now add a regression test"
acpterm -a opencode -r prompt "check the tests"
```

## Usage

```
acpterm [OPTIONS] COMMAND [ARGS]...
```

### Options

| Flag | Description |
|------|-------------|
| `-a, --agent <name>` | ACP agent binary to spawn (default: `opencode`) |
| `-s, --session <name>` | Session name for `prompt` (default: `default`) |
| `-r, --resume` | Resume the most recently used session for this project |
| `-m, --model <id>` | Override or set the active model for the session |
| `--mode <id>` | Override or set the active mode for the session |
| `-y, --yes` | Auto-approve all permission requests without prompting |
| `--read-only` | Run the agent in read-only mode (disables file modifications) |

### Commands

#### `prompt`

Send a prompt to an agent. Automatically creates or resumes the session.

```bash
acpterm -a opencode prompt "refactor the auth module"
acpterm -a opencode prompt -s api-session "implement token pagination"
acpterm -a opencode -r prompt "continue implementation"
acpterm -a opencode prompt -y "do stuff"              # auto-approve permissions
echo "explain this repo" | acpterm prompt             # pipe prompt from stdin
acpterm prompt --file prompt.md                       # load prompt from file
```

#### `exec`

One-shot prompt — no session persisted. Good for quick questions or CI scripts.

```bash
acpterm -a opencode exec "explain this codebase in one sentence"
```

## Supported Agents

Any binary that implements the [ACP protocol][acp] works out of the box:

```bash
acpterm -a opencode prompt "fix the lint errors"
acpterm -a kiro prompt "implement feature X"
acpterm -a agy prompt "review this PR"
```

## Example Output

```
$ acpterm -a opencode prompt "find the flaky test and fix it"

[thinking] Investigating test suite for flaky failures

[tool] Run npm test -- --reporter=verbose (pending)
[tool] Run npm test -- --reporter=verbose (completed)
  ✓ auth.login (0.8s)
  ✗ checkout.submit (timed out after 5000ms)
  ✓ cart.add (0.3s)

[thinking] Found it — checkout.submit has a race condition in the async setup

[tool] Edit src/checkout.test.ts (completed)
  Success. Updated 1 file.

[tool] Run npm test -- checkout.submit (completed)
  ✓ checkout.submit (0.4s)

Fixed: added `await` to the setup hook in checkout.submit. The test was
reading stale state from the previous run.

```

## Model & Mode Customization

> **Note:** `-m`/`--model` works via either `session/set_config_option` (standard) or `session/set_model` (newer protocol). Support varies by agent — opencode v1.2.24 supports `session/set_model` but not the older method. If an agent supports neither, the flag is silently ignored.

You can customize the LLM model and agent operating mode in two ways:

1. **Temporary Override**: Pass the global `-m` / `--model` or `--mode` options to override settings for the current command:
   ```bash
   acpterm -m gemini-2.5-flash --mode plan exec "explain this project"
   ```

2. **Persistent Default**: Set the default model or mode for an agent using the `models set` and `modes set` commands. This updates your configuration file so future sessions automatically run with them:
   ```bash
   acpterm models set gemini-2.5-pro
   acpterm modes set plan
   ```

Model and mode preferences are saved per-agent in `~/.acpterm/config.json` under the `default_models` and `default_modes` keys:
```json
{
  "default_models": {
    "opencode": "gemini-2.5-pro",
    "kiro": "kiro-large"
  },
  "default_modes": {
    "opencode": "plan"
  }
}
```

## Read-Only Mode

You can run agents in read-only mode by passing the `--read-only` flag globally:

```bash
acpterm --read-only exec "analyze the entry point and list its dependencies"
```

In read-only mode:
- The client advertises `writeTextFile: false` capability during the initial connection handshake. Compliant agents will automatically disable file-writing tools.
- If the agent attempts to call a write operation regardless, the client intercepts and blocks it, raising a runtime error to protect your codebase from any modifications.

## Prompt Size Guardrail

To protect against accidental high token consumption or costs:
- A default safety limit of **100,000 characters** is enforced on the total prompt payload.
- In interactive terminal sessions, exceeding this limit triggers a confirmation prompt (`[y/N]`) before proceeding.
- In piped/non-interactive sessions, the command will be blocked and exit with an error.

You can raise this limit by configuring `max_prompt_chars` in your configuration file (`~/.acpterm/config.json`):

```json
{
  "max_prompt_chars": 500000
}
```

## Lifecycle Hooks & Prompt Chaining

`acpterm` supports event-driven hooks that execute shell commands or chain follow-up prompts when an agent turn completes.

### 1. CLI Usage

Run a shell command or script when the turn completes using `-k` / `--on-turn-end`:

```bash
acpterm -a opencode exec "refactor the auth module" -k "uv run ruff format"
```

Automatically chain a follow-up prompt to the agent:

```bash
acpterm -a opencode exec "implement data validation" --chain-prompt "Now generate pytest unit tests for this code."
```

Dynamic Permission Policy Hooks (`-p` / `--on-permission`):

Evaluate file/command permissions dynamically using a policy script:

```bash
acpterm -a opencode exec "refactor code" -p "python3 scripts/permission_policy.py"
```

* **Exit Code 0**: Auto-Approve (Allowed)
* **Exit Code 1**: Auto-Deny (Cancelled)
* **Exit Code 2**: Fallback (Interactive terminal prompt)

Combine transcript export with performance monitoring:

```bash
SLOW_THRESHOLD_SECONDS=10.0 acpterm -a opencode exec "build the CLI features" \
  --export transcript.md \
  -k "python3 scripts/monitor_slow_response.py"
```

### 2. Environment Variables Passed to Hook Scripts

When a hook script runs, `acpterm` populates turn context as environment variables:

| Environment Variable | Description |
| :--- | :--- |
| `ACPTERM_EVENT` | Event trigger type (`turn_end`, `tool_call`, `permission`) |
| `ACPTERM_SESSION_ID` | Active session ID |
| `ACPTERM_AGENT` | Agent binary name (`opencode`, `kiro`, etc.) |
| `ACPTERM_STOP_REASON` | Turn stop reason (`end_turn`, `max_tokens`, `refusal`) |
| `ACPTERM_TRANSCRIPT` | Absolute path to exported Markdown transcript (if specified) |
| `ACPTERM_PROMPT` | Original prompt text sent in the turn |
| `ACPTERM_DURATION_SECONDS` | Total turn execution time in seconds |
| `ACPTERM_PERMISSION_TITLE` | Description of the requested permission (e.g. `Write text file src/auth.py`) |
| `ACPTERM_PERMISSION_KIND` | Permission operation category (`read`, `write`, `execute`, `delete`) |
| `ACPTERM_PERMISSION_PATH` | Path of file/resource affected by permission request |
| `ACPTERM_CWD` | Absolute path to project working directory |

### 3. Global Configuration

Define permanent default hooks in `~/.acpterm/config.json`:

```json
{
  "hooks": [
    {
      "name": "auto-format",
      "on": "turn_end",
      "run": "uv run ruff format"
    },
    {
      "name": "path-policy",
      "on": "permission",
      "run": "python3 scripts/permission_policy.py"
    },
    {
      "name": "slow-response-alert",
      "on": "turn_end",
      "condition": "duration > 10.0",
      "run": "python3 scripts/monitor_slow_response.py"
    }
  ]
}
```

## Session Storage

Sessions are persisted in `~/.acpterm/sessions.json`, keyed by agent name, working
directory, and session name. This lets you resume sessions across CLI invocations.

## Rejected / Questionable Features

*   ~~**Background Execution (`--no-wait`)**~~: Planned to allow enqueuing prompts without waiting for the response. Rejected because unattended background execution requires blind auto-approval of all agent actions (such as file modifications and shell commands), which presents significant safety risks, alongside process management complexity.

## Known Limitations

*   **Kiro file reading**: Kiro Engine v3 resolves `kiroFsReadFile: false` for unrecognized ACP clients. File reads are handled internally by kiro's Node adapter, but the `read_files` tool call status reports as blocked to the LLM, causing the agent to believe it cannot read files. Known working clients (e.g. `kiro-acp-telegram-bot`) suggest recognized client names may resolve this — the exact mechanism hasn't been identified.

[acp]: https://agentclientprotocol.com
