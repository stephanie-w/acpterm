"""Lifecycle hook runner and management for acpterm."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from rich.console import Console


_console = Console(highlight=False)


class HookDefinition(BaseModel):
    """Configuration schema for a single hook."""

    name: str | None = None
    on: str = Field(
        default="turn_end",
        description="Event trigger: turn_end, tool_call, or permission",
    )
    run: str | None = Field(default=None, description="Shell command string to execute")
    chain_prompt: str | None = Field(
        default=None, description="Follow-up prompt to chain to the agent"
    )
    condition: str | None = Field(
        default=None, description="Condition expression, e.g. stop_reason == 'end_turn'"
    )


class HookEvent(BaseModel):
    """Event data passed to hook execution."""

    event_type: str
    session_id: str | None = None
    agent_name: str = "opencode"
    stop_reason: str | None = None
    transcript_path: Path | None = None
    prompt_text: str | None = None
    duration_seconds: float | None = None
    cwd: Path = Field(default_factory=Path.cwd)
    extra: dict[str, Any] = Field(default_factory=dict)


def _eval_condition(condition: str, context: dict[str, Any]) -> bool:
    """Evaluate a simple condition expression against the context."""
    cond = condition.strip()
    # Simple equality check: var == 'value' or var == "value"
    if "==" in cond:
        left, right = cond.split("==", 1)
        var_name = left.strip()
        expected = right.strip().strip("'\"")
        actual = str(context.get(var_name, ""))
        return actual == expected
    # Simple inequality check: var != 'value'
    if "!=" in cond:
        left, right = cond.split("!=", 1)
        var_name = left.strip()
        expected = right.strip().strip("'\"")
        actual = str(context.get(var_name, ""))
        return actual != expected
    # Fallback to checking boolean presence
    return bool(context.get(cond))


async def run_hooks(
    event: HookEvent,
    hooks: list[HookDefinition],
    *,
    verbose: bool = False,
) -> list[str]:
    """Execute all hooks matching the given event.

    Args:
        event: The HookEvent containing runtime context.
        hooks: List of HookDefinition candidates to evaluate.
        verbose: Whether to print detailed execution logs.

    Returns:
        list[str]: A list of chained prompts collected from matching hooks.
    """
    chained_prompts: list[str] = []
    matching_hooks = [h for h in hooks if h.on == event.event_type]

    if not matching_hooks:
        return chained_prompts

    env = os.environ.copy()
    env["ACPTERM_EVENT"] = event.event_type
    env["ACPTERM_SESSION_ID"] = event.session_id or ""
    env["ACPTERM_AGENT"] = event.agent_name
    env["ACPTERM_STOP_REASON"] = event.stop_reason or ""
    env["ACPTERM_TRANSCRIPT"] = (
        str(event.transcript_path.absolute()) if event.transcript_path else ""
    )
    env["ACPTERM_PROMPT"] = event.prompt_text or ""
    env["ACPTERM_DURATION_SECONDS"] = (
        f"{event.duration_seconds:.2f}" if event.duration_seconds is not None else ""
    )
    env["ACPTERM_CWD"] = str(event.cwd.absolute())

    context = {
        "event": event.event_type,
        "session_id": event.session_id,
        "agent": event.agent_name,
        "stop_reason": event.stop_reason,
        "transcript": str(event.transcript_path) if event.transcript_path else "",
        "prompt": event.prompt_text,
        "duration": event.duration_seconds,
    }

    for hook in matching_hooks:
        hook_label = hook.name or hook.run or "unnamed_hook"

        if hook.condition and not _eval_condition(hook.condition, context):
            if verbose:
                _console.print(
                    f"[dim][hook][/dim] Skipped '{hook_label}' (condition '{hook.condition}' not met)"
                )
            continue

        if hook.run:
            if verbose:
                _console.print(f"[dim][hook][/dim] Executing '{hook_label}'...")

            try:
                proc = await asyncio.create_subprocess_shell(
                    hook.run,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                    cwd=event.cwd,
                )
                stdout, stderr = await proc.communicate()

                if proc.returncode == 0:
                    _console.print(
                        f"[green][hook:success][/green] {hook_label} (exit code 0)"
                    )
                    if verbose and stdout:
                        _console.print(f"[dim]{stdout.decode().strip()}[/dim]")
                else:
                    _console.print(
                        f"[red][hook:failed][/red] {hook_label} (exit code {proc.returncode})"
                    )
                    if stderr:
                        _console.print(f"[red]{stderr.decode().strip()}[/red]")
            except Exception as e:
                _console.print(
                    f"[red][hook:error][/red] Failed to run '{hook_label}': {e}"
                )

        if hook.chain_prompt:
            # Expand simple variables in chained prompt
            prompt_str = hook.chain_prompt.format(
                transcript_path=env["ACPTERM_TRANSCRIPT"],
                stop_reason=env["ACPTERM_STOP_REASON"],
                session_id=env["ACPTERM_SESSION_ID"],
                agent=env["ACPTERM_AGENT"],
            )
            chained_prompts.append(prompt_str)

    return chained_prompts
