# GEMINI Development Log

This file tracks the design decisions, tasks, and progress for improvements to the `acpterm` codebase.

## 📋 Task List

- [x] Add `pydantic` explicitly to `pyproject.toml` dependencies
- [x] Refactor `src/acpterm/config.py` to use Pydantic for structured configuration
- [x] Verify the refactoring with linting and type-checking (`just lint typecheck`)

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
