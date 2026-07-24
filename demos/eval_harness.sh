#!/usr/bin/env bash
# ============================================================================
# Eval Harness Demo — Two-Phase LLM Quality Testing
# ============================================================================
#
# This script demonstrates using acpterm as a test harness for evaluating
# whether an agent follows instructions (like AGENTS.md rules).
#
# Phase 1: Send a coding prompt to the agent, capture the full transcript
# Phase 2: Feed the transcript to an LLM judge that evaluates compliance
#
# Usage:
#   chmod +x demos/eval_harness.sh
#   ./demos/eval_harness.sh
#
#   # Custom agent (default: opencode):
#   AGENT=claude-code ./demos/eval_harness.sh
#
#   # Custom model for the judge:
#   JUDGE_MODEL=gemini-2.5-flash ./demos/eval_harness.sh
#
# ============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
AGENT="${AGENT:-opencode}"
JUDGE_MODEL="${JUDGE_MODEL:-}"  # empty = use agent default
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Create a workspace for eval artifacts
EVAL_DIR="${PROJECT_DIR}/demos/.eval_workspace"
mkdir -p "$EVAL_DIR"

# Colors for terminal output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
banner() {
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo -e "${BOLD}  $1${RESET}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo ""
}

info() {
    echo -e "${DIM}  ▸ $1${RESET}"
}

# ---------------------------------------------------------------------------
# Define the test prompt
# ---------------------------------------------------------------------------
# This prompt is designed to trigger several AGENTS.md anti-patterns
# if the agent doesn't follow the rules properly.

TEST_PROMPT="Write a Python module called 'data_loader.py' with the following:

1. A function that loads JSON files from a given directory path
2. A function that merges multiple config dictionaries, with an optional list of override keys
3. A retry wrapper function that retries a callable up to N times on failure
4. Proper error handling throughout

Important: This is for a Python 3.14+ project. Follow modern Python best practices."

# ---------------------------------------------------------------------------
# Define the judge instructions
# ---------------------------------------------------------------------------
# The judge instructions are codified in skills/llm_judge/SKILL.md.
# Here we just instruct the judge to apply those rules to the transcript.

JUDGE_PROMPT="Evaluate the attached session transcript against the rules defined in the attached llm-judge skill. Output a full report exactly in the format expected by the skill."

# ---------------------------------------------------------------------------
# Phase 1: Run the agent and capture transcript
# ---------------------------------------------------------------------------
banner "Phase 1: Agent Execution"

TRANSCRIPT="${EVAL_DIR}/transcript.md"

info "Agent:     ${AGENT}"
info "Prompt:    \"${TEST_PROMPT:0:60}...\""
info "Export to: ${TRANSCRIPT}"
echo ""

echo -e "${YELLOW}Running agent...${RESET}"
echo ""

acpterm exec "$TEST_PROMPT" \
    -a "$AGENT" \
    --export "$TRANSCRIPT" \
    --read-only \
    --yes

echo ""
echo -e "${GREEN}✓ Transcript saved to: ${TRANSCRIPT}${RESET}"
echo ""

# Quick stats
if [ -f "$TRANSCRIPT" ]; then
    LINES=$(wc -l < "$TRANSCRIPT")
    SIZE=$(wc -c < "$TRANSCRIPT")
    info "Transcript: ${LINES} lines, ${SIZE} bytes"

    # Show file operations if present
    if grep -q "## File Operations" "$TRANSCRIPT"; then
        echo ""
        echo -e "${DIM}  File operations recorded:${RESET}"
        sed -n '/^## File Operations$/,/^## /{ /^## File Operations$/d; /^## /d; p; }' "$TRANSCRIPT" | head -10
    fi
fi

# ---------------------------------------------------------------------------
# Phase 2: LLM Judge Evaluation
# ---------------------------------------------------------------------------
banner "Phase 2: LLM Judge Evaluation"

JUDGE_RESULT="${EVAL_DIR}/judge_result.md"
MODEL_FLAG=""
if [ -n "$JUDGE_MODEL" ]; then
    MODEL_FLAG="-m $JUDGE_MODEL"
    info "Judge model: ${JUDGE_MODEL}"
fi

info "Evaluating transcript using llm-judge skill..."
echo ""

echo -e "${YELLOW}Running judge...${RESET}"
echo ""

# shellcheck disable=SC2086
acpterm exec "$JUDGE_PROMPT" \
    -a "$AGENT" \
    --resource "skills/llm_judge/SKILL.md" \
    --resource "$TRANSCRIPT" \
    --export "$JUDGE_RESULT" \
    --read-only \
    --yes \
    $MODEL_FLAG

echo ""
echo -e "${GREEN}✓ Judge result saved to: ${JUDGE_RESULT}${RESET}"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
banner "Eval Complete"

echo -e "  ${BOLD}Artifacts:${RESET}"
echo -e "    Agent transcript: ${DIM}${TRANSCRIPT}${RESET}"
echo -e "    Judge result:     ${DIM}${JUDGE_RESULT}${RESET}"
echo ""
echo -e "  ${BOLD}Quick review:${RESET}"
echo -e "    ${DIM}cat ${TRANSCRIPT}${RESET}"
echo -e "    ${DIM}cat ${JUDGE_RESULT}${RESET}"
echo ""
