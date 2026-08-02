# Self-Healing Refactoring & Test Loops

This guide demonstrates how to use `acpterm`'s event hooks (`-k / --on-turn-end`) and prompt chaining (`--chain-prompt`) to construct automated test-driven refactoring loops.

---

## 💡 How Prompt Chaining Works

When an agent completes a turn:
1. `acpterm` executes the shell command specified in `-k` / `--on-turn-end` (e.g. running your test suite).
2. If `--chain-prompt` is specified, `acpterm` automatically initiates a follow-up turn in the same session, passing test results or context back to the agent.

---

## 🚀 Example: Test-Driven Refactoring Loop

### Step 1: Execute Initial Refactoring Request

```bash
acpterm -a opencode exec "Refactor src/auth.py to use modern async/await patterns" \
  -k "uv run pytest tests/test_auth.py" \
  --chain-prompt "The test suite run completed with exit code {exit_code}. If any tests failed, analyze the failure and update the implementation to fix it."
```

---

## 🛠️ Advanced Self-Healing Hook Script

For multi-stage verification (e.g. running formatting, linting, and tests), use a dedicated post-turn script:

### `scripts/verify_and_chain.py`

```python
#!/usr/bin/env python3
import os
import subprocess
import sys

def verify_codebase():
    print("🔍 Running codebase verification...")

    # Run ruff format & linter
    lint_res = subprocess.run(["uv", "run", "ruff", "check", "."], capture_output=True, text=True)
    if lint_res.returncode != 0:
        print("[verify:fail] Linting errors found!")
        print(lint_res.stdout)
        sys.exit(1)

    # Run pytest
    test_res = subprocess.run(["uv", "run", "pytest", "tests/"], capture_output=True, text=True)
    if test_res.returncode != 0:
        print("[verify:fail] Test suite failed!")
        print(test_res.stdout)
        sys.exit(1)

    print("✅ All verifications passed!")
    sys.exit(0)

if __name__ == "__main__":
    verify_codebase()
```

### Command Execution:

```bash
acpterm -a opencode exec "Implement rate limiting middleware" \
  --export transcript.md \
  -k "python3 scripts/verify_and_chain.py" \
  --chain-prompt "Verification failed. Review the errors in the output above and fix the issues."
```
