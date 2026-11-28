from __future__ import annotations

import json
from pathlib import Path

import pytest

from acpterm import session_store


@pytest.fixture
def sessions_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "state" / "sessions.json"
    monkeypatch.setattr(session_store, "SESSIONS_FILE", path)
    return path


def test_save_writes_expected_entry_and_leaves_no_temp_file(
    sessions_file: Path,
) -> None:
    session_store.save("agent", "/repo", "sid-1", "default")

    data = json.loads(sessions_file.read_text(encoding="utf-8"))
    entry = data["agent:/repo:default"]
    assert entry["agent_name"] == "agent"
    assert entry["cwd"] == "/repo"
    assert entry["name"] == "default"
    assert entry["session_id"] == "sid-1"
    assert sorted(p.name for p in sessions_file.parent.iterdir()) == ["sessions.json"]


def test_write_failure_removes_temp_file(
    sessions_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(session_store.os, "replace", failing_replace)

    with pytest.raises(OSError, match="replace failed"):
        session_store.save("agent", "/repo", "sid-1")

    assert list(sessions_file.parent.iterdir()) == []


def test_write_dump_failure_removes_temp_file(
    sessions_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_dump(*_args: object, **_kwargs: object) -> None:
        raise TypeError("serialize failed")

    monkeypatch.setattr(session_store.json, "dump", failing_dump)

    with pytest.raises(TypeError, match="serialize failed"):
        session_store.save("agent", "/repo", "sid-1")

    assert list(sessions_file.parent.iterdir()) == []


def test_read_recovers_from_invalid_utf8(
    sessions_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sessions_file.parent.mkdir(parents=True, exist_ok=True)
    sessions_file.write_bytes(b"\xff\xfe\x00")

    assert session_store.list_sessions() == []
    assert "Warning" in capsys.readouterr().err


@pytest.mark.parametrize(
    "content",
    ["{not json", "[]", '"text"', "null"],
    ids=["corrupt", "list", "string", "null"],
)
def test_read_recovers_from_invalid_json(
    sessions_file: Path, capsys: pytest.CaptureFixture[str], content: str
) -> None:
    sessions_file.parent.mkdir(parents=True, exist_ok=True)
    sessions_file.write_text(content, encoding="utf-8")

    assert session_store.list_sessions() == []
    assert "Warning" in capsys.readouterr().err


def test_list_sessions_skips_malformed_entries(sessions_file: Path) -> None:
    sessions_file.parent.mkdir(parents=True, exist_ok=True)
    sessions_file.write_text(
        json.dumps(
            {
                "good": {
                    "agent_name": "agent",
                    "cwd": "/repo",
                    "session_id": "sid-good",
                },
                "non-dict": ["not", "a", "dict"],
                "missing-cwd": {"agent_name": "agent", "session_id": "sid-x"},
                "missing-agent": {"cwd": "/repo", "session_id": "sid-y"},
            }
        ),
        encoding="utf-8",
    )

    assert len(session_store.list_sessions()) == 3
    assert [
        e["session_id"]
        for e in session_store.list_sessions(agent_name="agent", cwd="/repo")
    ] == ["sid-good"]


def test_get_skips_malformed_entry(sessions_file: Path) -> None:
    sessions_file.parent.mkdir(parents=True, exist_ok=True)
    key = "agent:/repo:default"
    sessions_file.write_text(
        json.dumps({key: {"agent_name": "agent", "cwd": "/repo"}}), encoding="utf-8"
    )

    assert session_store.get("agent", "/repo") is None

    sessions_file.write_text(json.dumps({key: []}), encoding="utf-8")
    assert session_store.get("agent", "/repo") is None


def test_get_latest_skips_malformed_entries(sessions_file: Path) -> None:
    sessions_file.parent.mkdir(parents=True, exist_ok=True)
    sessions_file.write_text(
        json.dumps(
            {
                "broken": "not-a-dict",
                "no-id": {"agent_name": "agent", "cwd": "/repo"},
                "valid": {
                    "agent_name": "agent",
                    "cwd": "/repo",
                    "session_id": "sid-valid",
                    "last_used_at": "2024-01-01T00:00:00+00:00",
                },
            }
        ),
        encoding="utf-8",
    )

    latest = session_store.get_latest("agent", "/repo")

    assert latest is not None
    assert latest["session_id"] == "sid-valid"


def test_save_replaces_non_dict_entry(sessions_file: Path) -> None:
    sessions_file.parent.mkdir(parents=True, exist_ok=True)
    sessions_file.write_text(
        json.dumps({"agent:/repo:default": "corrupt"}), encoding="utf-8"
    )

    session_store.save("agent", "/repo", "sid-new")

    assert session_store.get("agent", "/repo") == "sid-new"
