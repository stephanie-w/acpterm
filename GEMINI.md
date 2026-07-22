# GEMINI Development Log

This file tracks the design decisions, tasks, and progress for improvements to the `acpterm` codebase.

## 📋 Task List

- [x] Add `pydantic` explicitly to `pyproject.toml` dependencies
- [x] Refactor `src/acpterm/config.py` to use Pydantic for structured configuration
- [x] Verify the refactoring with linting and type-checking (`just lint typecheck`)
- [x] Consolidate `_AgentClient` and `_SilentClient` into a single `AgentClient` class in `src/acpterm/acp_agent.py`
- [x] Implement `ClientCapabilities` advertisement in `initialize` phase (supporting `fs` and `session.configOptions.boolean`)
- [x] Implement `--read-only` flag in CLI and `read_only` capability constraint in agent runner
- [x] Fix the `False` value serialization bug in `agent_cache.py`
- [x] Document `--read-only` flag and capabilities in `README.md`
- [x] Make `ACPAgent.stop()` robust against cleanup exceptions
- [x] Implement actual agent session closure in `sessions close` CLI command
- [x] Add model lists to Pydantic configuration class in `src/acpterm/config.py`
- [x] Implement `acpterm models set <model_id>` CLI command in `src/acpterm/cli.py`
- [x] Add config-based fallback to `acpterm models list` rendering in `src/acpterm/cli.py`
- [x] Implement stdin prompt piping (`echo "..." | acpterm prompt`)
- [x] Implement file-based prompt input via `--file / -f` option
- [x] Implement `acpterm modes list` and `acpterm modes set <mode_id>` commands for agent mode switching
- [x] Implement structured interactive form elicitation (`fs/create_elicitation`) using Rich terminal prompts
- [x] Implement config show / config init commands for profile configuration management
- [x] Implement `acpterm commands list` for agent slash command discovery
- [x] Implement self-healing session loading/resuming with automatic fallback to new session on stale ID errors
- [x] Implement `session/resume` vs `session/load` ACP protocol capability checking
- [x] Implement `-r / --resume` flag to automatically target the most recently used session
- [x] Track `last_used_at` timestamp in `session_store.py`
- [x] Simplify CLI interface by removing redundant `sessions new` command
- [x] Implement automatic silent cache sync on session creation and loading
- [x] Remove redundant `--refresh` options across list commands
- [x] Implement mode and model validation with fuzzy matching ("Did you mean...?")
- [x] Implement shell `TAB` auto-completion callbacks for `-a`, `-s`, `-m`, and `--mode`

## 🛠️ Configuration Redesign

### Old Implementation
Configuration was read as an untyped dictionary via:
```python
def _read() -> dict[str, Any]:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}
```
This provided no automatic type validation or clear configuration structure.

### New Implementation
We will use a Pydantic model (`Config`) to represent the configuration layout:
```python
from pydantic import BaseModel, Field

class Config(BaseModel):
    agents: dict[str, str] = Field(default_factory=dict)
```
This structure ensures:
1. The `agents` dictionary is validated as containing string keys and string values.
2. Safe initialization with default values if the configuration file is empty or missing.
3. Safe validation of custom JSON inputs.

## 📚 Codebase Reference & ACP Documentation

For future development sessions, the codebase is structured as follows:

*   **`src/acpterm/cli.py`**: The CLI entrypoint utilizing `typer`. Registers CLI subcommands for models, modes, sessions, prompting, and exec.
*   **`src/acpterm/acp_agent.py`**: Handles connecting to the ACP agent process, lifecycle initialization, session load/creation, and mapping client capability callbacks.
*   **`src/acpterm/elicitation.py`**: Implements form rendering, UI prompts (via `rich.prompt`), validation, and defaults logic for structured client elicitations.
*   **`src/acpterm/agent_cache.py`**: Manages a local cache of agent config options, models, and modes with TTL expiration.

### ACP Protocol Docs

Detailed references for ACP connection flows, schemas, and capabilities are documented locally in:
*   [schema.md](file:///home/stephanie/DEV/acpterm/docs/acp-protocol/schema.md): Complete list of RPC methods, requests, and response models.
*   [prompt-turn.md](file:///home/stephanie/DEV/acpterm/docs/acp-protocol/prompt-turn.md): Lifecycle of prompt turns, cancellation, and sequence flow.
*   [session-modes.md](file:///home/stephanie/DEV/acpterm/docs/acp-protocol/session-modes.md): Agent operating modes and switching protocols.
*   [file-system.md](file:///home/stephanie/DEV/acpterm/docs/acp-protocol/file-system.md): File system capability schema.
*   [terminals.md](file:///home/stephanie/DEV/acpterm/docs/acp-protocol/terminals.md): Interactive terminal subprocess details.

