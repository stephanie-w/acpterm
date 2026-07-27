# TODO

## Next

### Piping & File Input
- [x] `echo '...' | acpterm -a <agent> prompt` — prompt from stdin
- [x] `acpterm -a <agent> prompt --file prompt.md` — prompt from file

### Verbose / Debug
- [x] `-v/--verbose` logs initialize, new_session/load_session, and prompt JSON responses
- [x] Works across all commands (`prompt`, `exec`, `sessions new`, `models list`)

## Later

- [ ] Agent as positional arg (`acpterm opencode prompt '...'`)
- [ ] Implicit prompt (`acpterm opencode 'fix the tests'`)
- [x] ~~`--no-wait` flag — enqueue without waiting~~ (Abandoned: background runs require auto-approval, presenting high safety and process management risks)
- [ ] `cancel` command — cancel in-flight prompt
- [x] `config show` / `config init` commands
- [ ] `sessions history [name]` — show recent turn history
- [ ] `sessions ensure [--name NAME]` — return existing or create
- [ ] `status` — local process status
- [x] Support slash commands discovery and execution (listing available slash commands and invoking them)
- [x] Export session run transcript (Tee-like clean markdown format, or structured JSON logging of method calls/tool updates while excluding micro-streaming wire noise) (`--export <file>`)
- [ ] Dynamic shell autocompletion for `-a` (agents), `-s` (sessions), and `models set` (models)
- [x] Render agent plan updates — display `plan` session updates (entries with content/priority/status) as a checklist in the terminal. Also recorded in transcript exports. **Tests needed.**
- [x] Graceful terminal content block display — when a `tool_call_update` contains `{"type": "terminal", "terminalId": "..."}` content, render a labeled tag instead of `repr()` fallback. **Tests needed.**

### Abandoned / Won't Implement

- ~~Support v1 terminal capabilities (`terminal/*` RPC methods)~~ — **Won't implement.** The v1 terminal surface (`terminal/create`, `terminal/output`, `terminal/wait_for_exit`, `terminal/kill`, `terminal/release`) requires the Client to spawn and manage subprocesses on behalf of the Agent. This entire API is removed in ACP v2. Neither opencode nor kiro delegates command execution to the Client — both run commands in their own process and stream results as text in `tool_call_update`. The current dummy stubs in `AgentClient` (returning empty output and exit code 0) are never actually exercised and are harmless. See "ACP v2 Migration" section below for the replacement model.

## ACP v2 Migration

Reference: `docs/acp-protocol/v2/migration.md`

### Overview

ACP v2 is a consolidation release. acpterm must support **both v1 and v2 side-by-side** via version negotiation (Client sends `protocolVersion: 2`, Agent responds with 2 if supported, else 1). Gate v2 behind feature flags until stable.

### Blocker

The `acp` Python library (`acp-sdk`) does not reference v2 yet — no v2 schema types, no v2-aware `ClientSideConnection`. Implementation is **blocked until the library adds v2 support**. Track upstream and revisit when a v2-compatible release lands.

### Architectural Decision: Pragmatic Strategy Pattern (Option B)

**Decision**: Use a focused strategy split in the areas where v1 and v2 diverge structurally, while keeping simple branches or additive handlers elsewhere. This avoids both scattered if/else spaghetti and over-engineered full abstractions. The pattern also generalizes to future protocol versions.

**Concretely, the split looks like this:**

1. **`ACPAgent` stores `self._protocol_version: int`** after negotiation in `initialize`. All version-aware decisions flow from this single source of truth.

2. **Prompt flow — version-specific helpers** (the one truly structural divergence):
   - `_prompt_v1()`: awaits the `session/prompt` response, extracts `stopReason` from the result (current behavior).
   - `_prompt_v2()`: sends `session/prompt`, receives immediate `{}` ack, then waits for the `state_update` notification with `state: "idle"` + `stopReason`.
   - `send_prompt()` delegates to the right one based on `self._protocol_version`.

3. **Capability construction — factory function**:
   - `_build_capabilities(version: int, *, read_only: bool) -> ClientCapabilities` in `acp_agent.py`.
   - v1: current shape (`fs`, `terminal`, `session.configOptions`, `plan`, `elicitation`).
   - v2: `capabilities` + required `info`, no `fs`/`terminal`, objects instead of booleans.

4. **Session update rendering — purely additive** (no branching needed):
   - `format_session_update` in `output.py` already dispatches on the `sessionUpdate` string.
   - Add handlers for v2-only types (`state_update`, `terminal_update`, `terminal_output_chunk`, `plan_update`, `tool_call_content_chunk`) alongside existing v1 handlers.
   - v1 types that v2 drops (`tool_call`, `current_mode_update`) stay — they simply won't fire in v2 sessions.

5. **Session load/resume — one branch** in `ACPAgent.start()`:
   - v1: `session/load` with `session_id`.
   - v2: `session/resume` with `session_id` + `replayFrom: { type: "start" }`.

6. **Client method stubs (fs/terminal) — keep as-is**:
   - The `acp` library likely expects them registered on the client object.
   - In v2, they're simply never called by the agent. Harmless dead code.

7. **Modes — thin branch** in `set_mode()` and CLI `modes` commands:
   - v1: `session/set_mode`.
   - v2: `session/set_config_option` with `category: "mode"`.

**Why not full strategy classes?** The codebase is small and pragmatic. Two version-specific prompt helpers + a capability factory + additive update handlers cover 90% of the split. Full `V1Protocol`/`V2Protocol` classes would add indirection without proportional benefit at this scale.

**Why not scattered if/else?** The prompt lifecycle is fundamentally different between v1 and v2 (sync response vs async notification). Mixing both flows in a single `send_prompt()` method would be hard to follow and easy to break.

**Future versions**: If v3 arrives, add `_prompt_v3()`, extend `_build_capabilities`, add new update handlers. The pattern scales without requiring a rewrite.

### Key Breaking Changes

#### 1. Prompt lifecycle redesign (highest impact)

- **v1**: `session/prompt` stays pending for the entire turn. The response carries the `stopReason`.
- **v2**: `session/prompt` returns `{}` immediately (acknowledgment only). Turn progress and completion arrive as `session/update` notifications:
  - `state_update` with `state: "running"` — foreground work started
  - `state_update` with `state: "requires_action"` — blocked on user action (e.g. permission)
  - `state_update` with `state: "idle"` + `stopReason` — turn ended
- **Migration**: The core prompt flow in `acp_agent.py` (`send_prompt`) and `cli.py` must detect the negotiated version and either await the response (v1) or listen for the idle `state_update` (v2).

#### 2. Client fs & terminal execution removed

- **v1**: Client advertises `fs` and `terminal` capabilities. Agent calls `fs/read_text_file`, `fs/write_text_file`, `terminal/create`, etc.
- **v2**: All removed. No `clientCapabilities.fs`, no `clientCapabilities.terminal`. If the Agent needs Client-side tools, the Client provides an **MCP server** via `mcpServers` on `session/new` or `session/resume`.
- **Impact on `--read-only`**: In v1, `--read-only` set `writeTextFile: false` and blocked writes in the `write_text_file` handler. In v2, there's no fs capability to toggle. Read-only enforcement would need to happen via MCP server configuration or not providing a write-capable MCP server at all.
- **Impact on permissions**: Permission denial for commands has always been advisory (honor-based). The agent runs commands in its own process; acpterm can only say "I'd prefer you didn't." This doesn't change in v2.

#### 3. Terminal display is Agent-owned (v2 only, display-only)

- **v2 model**: The Agent runs commands itself and sends output to the Client for display via:
  - `terminal_update` — upsert keyed by `terminalId`, carries `command`, `cwd`, base64 `output` snapshot, `exitStatus`
  - `terminal_output_chunk` — appends base64-encoded bytes for live streaming
- **Client responsibility**: Decode and render the bytes. No input, no kill, no lifecycle control. Display-only.
- **This is the terminal rendering that matters** — not the v1 subprocess management.

#### 4. Session modes become config options

- **v1**: Dedicated `session/set_mode`, `current_mode_update`, `modes` on session response.
- **v2**: All removed. Modes are just `session/set_config_option` with `category: "mode"`. `current_mode_update` replaced by `config_option_update`.
- **Impact**: `set_mode()` in `acp_agent.py` and `modes list` / `modes set` in CLI need v2 branches that use config options instead.

#### 5. Tool call creation merged into tool_call_update

- **v1**: Separate `tool_call` (create) and `tool_call_update` (modify) session updates.
- **v2**: Only `tool_call_update`. First update with an unseen `toolCallId` creates it. Adds `tool_call_content_chunk` for streaming.
- **Impact**: `format_session_update` in `output.py` and transcript recording in `acp_agent.py` handle both `tool_call` and `tool_call_update` — in v2, only `tool_call_update` arrives.

#### 6. Other changes

- `initialize`: `clientCapabilities` / `agentCapabilities` → `capabilities`. `clientInfo` / `agentInfo` → `info` (required).
- `session/load` removed → use `session/resume` with `replayFrom: { type: "start" }`.
- `authenticate` → `auth/login`.
- Message IDs required on all chunks.
- Plan updates gain `planId` and become `plan_update` with tagged union.
- Capability booleans become objects (`true` → `{}`).
- Config option `id` → `configId`.

### Migration TODO

- [ ] Add v2 version negotiation — send `protocolVersion: 2` in `initialize`, detect response version, store negotiated version on `ACPAgent`
- [ ] v2 prompt lifecycle — handle immediate `{}` ack + `state_update` notifications for turn completion
- [ ] v2 terminal display — render `terminal_update` and `terminal_output_chunk` session updates (base64 decode + console output)
- [ ] v2 session resume — replace `session/load` with `session/resume` + `replayFrom`
- [ ] v2 capability restructure — `capabilities` + required `info`, no more `fs`/`terminal` client capabilities
- [ ] v2 tool call unification — handle `tool_call_update`-only flow (no separate `tool_call` creation event)
- [ ] v2 modes as config options — route mode operations through `session/set_config_option` with `category: "mode"`
- [ ] v2 plan updates — handle `plan_update` with `planId` and `type` discriminator
- [ ] Provide MCP server for Client-side file access (replacement for v1 `fs/*` methods)

## Agent Authentication Architecture (Pluggable Auth Providers)

Extension-based authentication requests (like `_kiro/auth/getAccessToken`) are resolved via:
1. `EnvAuthProvider` — reads `ACPTERM_<AGENT>_TOKEN`, `ACPTERM_AUTH_TOKEN`, or `AWS_*` environment variables.
2. `CommandAuthProvider` — executes `token_commands` from `~/.acpterm/config.json`, captures stdout, parses JSON.

```
src/acpterm/auth/
├── base.py              # AuthProvider abstract class
├── env.py               # Environment variable provider
├── command.py           # Config token_command provider

scripts/
└── kiro-auth.py         # Standalone Kiro token extractor (reads ~/.kiro auth DB,
                            scans logs for profileArn, outputs JSON to stdout).
                            Used via: "token_commands": {"kiro": "python3 scripts/kiro-auth.py"}
```

### Design Decision: Utility Scripts over Library Providers

Vendor-specific auth extraction (like reading Kiro's SQLite DB and log files) belongs in standalone scripts invoked via `CommandAuthProvider`, not in the core library. This keeps `acpterm` free of agent-specific DB/log dependencies while maintaining the same auth interface.

## Issues & Investigation

### Model Discovery
- **Current state**: Cache reads `config_options` from `new_session` response. Works for standard agents.
- **Issue**: opencode stores model in `_meta.opencode.modelId` with `availableVariants`, not in `config_options[id="model"]`.
- **Needed**: Parse agent-specific `_meta` fields. May differ per agent — need a mapping or agent-specific parser.
- **Reactive display attempt**: Tried `config_option_update` / `current_mode_update` notifications — agent doesn't emit these at session start. Reverted to cache approach.

### Protocol Compatibility
- **Model setting (`session/set_config_option` vs `session/set_model`)**: The ACP protocol evolved from models-as-config-options (`session/set_config_option` with `config_id="model"`) to a dedicated `session/set_model` method (`sessionId` + `modelId`). The `acp` Python library v0.11.0 (schema v1.16.0) only exposes the old method. `acpterm` tries both: first `session/set_config_option`, then falls back to raw `session/set_model` via `send_request`. opencode v1.2.24 supports `session/set_model` but not `session/set_config_option`. When upgrading `agent-client-protocol`, prefer the library's native `set_session_model()` once available.
- **`session/close`**: Optional method. Wrapped in try/except. Session cleanup relies on process termination when close isn't supported.
- **`_SilentClient`** (`cli.py`): Duplicates `_AgentClient` logic. Should be shared or extracted into a common base.

### Reverted Features
- **Model/mode display at prompt start**: Removed (notifications not emitted by agent). Replaced by `models list` with cache.

## Done

- [x] `prompt` command with session persistence
- [x] `exec` command for one-shot (no persistence)
- [x] `-s/--session` flag for named sessions
- [x] `-y/--yes` flag for auto-approve permissions
- [x] `-a/--agent` flag (root-level)
- [x] Agent config via `~/.acpterm/config.json`
- [x] `sessions new`, `sessions list`, `sessions show`, `sessions close`
- [x] `models list [--refresh]` with 7-day cache
- [x] Streaming thinking display
- [x] Tool output with truncated file content
- [x] Markdown rendering via `rich`
- [x] Context usage and cost display at end of run
- [x] `-v/--verbose` on `models list` (dumps `new_session` JSON)
