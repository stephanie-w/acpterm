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
