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
- [ ] `--no-wait` flag — enqueue without waiting
- [ ] `cancel` command — cancel in-flight prompt
- [ ] `config show` / `config init` commands
- [ ] `sessions history [name]` — show recent turn history
- [ ] `sessions ensure [--name NAME]` — return existing or create
- [ ] `status` — local process status
- [ ] Support slash commands discovery and execution (listing available slash commands and invoking them)
- [ ] Export session run transcript (Tee-like clean markdown format, or structured JSON logging of method calls/tool updates while excluding micro-streaming wire noise) (`--export <file>`)
- [ ] Dynamic shell autocompletion for `-a` (agents), `-s` (sessions), and `models set` (models)

## Issues & Investigation

### Model Discovery
- **Current state**: Cache reads `config_options` from `new_session` response. Works for standard agents.
- **Issue**: opencode stores model in `_meta.opencode.modelId` with `availableVariants`, not in `config_options[id="model"]`.
- **Needed**: Parse agent-specific `_meta` fields. May differ per agent — need a mapping or agent-specific parser.
- **Reactive display attempt**: Tried `config_option_update` / `current_mode_update` notifications — agent doesn't emit these at session start. Reverted to cache approach.

### Protocol Compatibility
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
