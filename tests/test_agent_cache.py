from __future__ import annotations

import json
from pathlib import Path

from acp import schema as acp_schema
import pytest

from acpterm import agent_cache


@pytest.fixture
def cache_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "state" / "agent_cache.json"
    monkeypatch.setattr(agent_cache, "CACHE_FILE", path)
    return path


def test_store_writes_expected_entry_and_leaves_no_temp_file(
    cache_file: Path,
) -> None:
    agent_cache.store(
        "agent",
        commands=[acp_schema.AvailableCommand(name="compact", description="Summarize")],
    )

    data = json.loads(cache_file.read_text(encoding="utf-8"))
    assert data["agent"]["commands"] == [
        {"name": "compact", "description": "Summarize"}
    ]
    assert sorted(p.name for p in cache_file.parent.iterdir()) == ["agent_cache.json"]


def test_write_failure_removes_temp_file(
    cache_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(agent_cache.os, "replace", failing_replace)

    with pytest.raises(OSError, match="replace failed"):
        agent_cache.store(
            "agent",
            commands=[acp_schema.AvailableCommand(name="compact", description="")],
        )

    assert list(cache_file.parent.iterdir()) == []


def test_write_dump_failure_removes_temp_file(
    cache_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_dump(*_args: object, **_kwargs: object) -> None:
        raise TypeError("serialize failed")

    monkeypatch.setattr(agent_cache.json, "dump", failing_dump)

    with pytest.raises(TypeError, match="serialize failed"):
        agent_cache.store(
            "agent",
            commands=[acp_schema.AvailableCommand(name="compact", description="")],
        )

    assert list(cache_file.parent.iterdir()) == []


def test_read_recovers_from_invalid_utf8(
    cache_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_bytes(b"\xff\xfe\x00")

    assert agent_cache.is_fresh("agent") is False
    assert agent_cache.get_commands("agent") is None
    assert "Warning" in capsys.readouterr().err


@pytest.mark.parametrize(
    "content",
    ["{not json", "[]", '"text"', "null"],
    ids=["corrupt", "list", "string", "null"],
)
def test_read_recovers_from_invalid_json(
    cache_file: Path, capsys: pytest.CaptureFixture[str], content: str
) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(content, encoding="utf-8")

    assert agent_cache.get_commands("agent") is None
    assert "Warning" in capsys.readouterr().err


def test_store_with_none_commands_preserves_cached_commands(cache_file: Path) -> None:
    agent_cache.store(
        "agent",
        commands=[acp_schema.AvailableCommand(name="compact", description="Summarize")],
    )

    agent_cache.store("agent", commands=None)

    assert agent_cache.get_commands("agent") == [
        {"name": "compact", "description": "Summarize"}
    ]


def test_store_with_commands_updates_cached_commands(cache_file: Path) -> None:
    agent_cache.store(
        "agent", commands=[acp_schema.AvailableCommand(name="old", description="Old")]
    )

    agent_cache.store(
        "agent", commands=[acp_schema.AvailableCommand(name="new", description="New")]
    )

    assert agent_cache.get_commands("agent") == [{"name": "new", "description": "New"}]


def test_store_replaces_non_dict_entry(cache_file: Path) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({"agent": "corrupt"}), encoding="utf-8")

    agent_cache.store(
        "agent",
        commands=[acp_schema.AvailableCommand(name="compact", description="Summarize")],
    )

    assert agent_cache.get_commands("agent") == [
        {"name": "compact", "description": "Summarize"}
    ]
