#!/usr/bin/env bash
# ============================================================================
# Hooks System Demo — Post-Turn Scripting & Prompt Chaining
# ============================================================================
#
# This script demonstrates using acpterm hooks:
# 1. Executing shell commands/scripts on turn completion (--on-turn-end / -k)
# 2. Automatically chaining follow-up prompts (--chain-prompt)
# 3. Accessing ACPTERM_* environment variables in hooks
#
# Usage:
#   chmod +x demos/hooks_demo.sh
#   ./demos/hooks_demo.sh
#
# ============================================================================

set -euo pipefail

AGENT="${AGENT:-opencode}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DEMO_DIR="${PROJECT_DIR}/demos/.hooks_workspace"
mkdir -p "$DEMO_DIR"

TRANSCRIPT="${DEMO_DIR}/transcript.md"

echo "Running acpterm exec with --on-turn-end hook and --chain-prompt..."
echo ""

acpterm exec "Write a python function that adds two numbers" \
    -a "$AGENT" \
    --read-only \
    --yes \
    --export "$TRANSCRIPT" \
    --on-turn-end "echo '[HOOK FIRING] Event: \$ACPTERM_EVENT | Agent: \$ACPTERM_AGENT | Stop Reason: \$ACPTERM_STOP_REASON'" \
    --chain-prompt "Summarize your previous response in one sentence."

echo ""
echo "Done!"
