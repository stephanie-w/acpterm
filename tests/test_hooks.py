"""Tests for lifecycle hook execution."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from acpterm.hooks import HookDefinition, HookEvent, run_permission_hook


def _permission_event(cwd: Path) -> HookEvent:
    return HookEvent(event_type="permission", cwd=cwd)


def _permission_hooks(*commands: str) -> list[HookDefinition]:
    return [
        HookDefinition(name=f"hook-{index}", on="permission", run=command)
        for index, command in enumerate(commands)
    ]


@pytest.mark.asyncio
async def test_unexpected_exit_continues_to_later_approving_hook(
    tmp_path: Path,
) -> None:
    """A broken hook must not prevent a later hook from approving."""
    hooks = _permission_hooks("exit 127", "exit 0")

    result = await run_permission_hook(_permission_event(tmp_path), hooks)

    assert result == 0


@pytest.mark.asyncio
async def test_unexpected_exit_continues_to_later_denying_hook(tmp_path: Path) -> None:
    """A broken hook must not prevent a later hook from denying."""
    hooks = _permission_hooks("exit 127", "exit 1")

    result = await run_permission_hook(_permission_event(tmp_path), hooks)

    assert result == 1


@pytest.mark.asyncio
async def test_all_unexpected_exits_fall_through(tmp_path: Path) -> None:
    """If every hook is broken, the caller keeps its normal fallback path."""
    hooks = _permission_hooks("exit 127", "exit 42")

    result = await run_permission_hook(_permission_event(tmp_path), hooks)

    assert result is None


@pytest.mark.asyncio
async def test_unexpected_exit_then_fallback_hook_returns_fallback(
    tmp_path: Path,
) -> None:
    """A later hook's exit code still wins after a broken hook."""
    hooks = _permission_hooks("exit 127", "exit 2")

    result = await run_permission_hook(_permission_event(tmp_path), hooks)

    assert result == 2


@pytest.mark.asyncio
async def test_launch_failure_continues_to_later_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hook that cannot be launched is skipped like an unexpected exit."""
    real_create = asyncio.create_subprocess_shell

    async def flaky_create(command: str, **kwargs: Any) -> Any:
        if command == "cannot-launch":
            raise FileNotFoundError(command)
        return await real_create(command, **kwargs)

    monkeypatch.setattr("acpterm.hooks.asyncio.create_subprocess_shell", flaky_create)
    hooks = _permission_hooks("cannot-launch", "exit 0")

    result = await run_permission_hook(_permission_event(tmp_path), hooks)

    assert result == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("exit 0", 0),
        ("exit 1", 1),
        ("exit 2", 2),
    ],
)
async def test_defined_exit_codes_keep_their_meaning(
    command: str, expected: int, tmp_path: Path
) -> None:
    """The documented 0/1/2 contract is unchanged."""
    result = await run_permission_hook(
        _permission_event(tmp_path), _permission_hooks(command)
    )

    assert result == expected
