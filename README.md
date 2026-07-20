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
acpterm -a opencode new --name my-session
acpterm -a opencode prompt -s my-session "find the flaky test and fix it"
```

Send follow-up prompts in the same session:

```bash
acpterm -a opencode prompt -s my-session "now add a regression test"
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
| `-y, --yes` | Auto-approve all permission requests without prompting |
| `--read-only` | Run the agent in read-only mode (disables file modifications) |

### Commands

#### `prompt`

Send a prompt to an agent. Saves the session for subsequent prompts.

```bash
acpterm -a opencode prompt "refactor the auth module"
acpterm -a opencode prompt -s api-session "implement token pagination"
acpterm -a opencode prompt -y "do stuff"              # auto-approve permissions
echo "explain this repo" | acpterm prompt             # pipe prompt from stdin
acpterm prompt --file prompt.md                       # load prompt from file
```

#### `new`

Create a new session (saves the session ID locally at `~/.acpterm/sessions.json`).

```bash
acpterm -a opencode new                               # default session
acpterm -a opencode new --name backend                # named session
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

## Session Storage

Sessions are persisted in `~/.acpterm/sessions.json`, keyed by agent name, working
directory, and session name. This lets you resume sessions across CLI invocations.

[acp]: https://agentclientprotocol.com
