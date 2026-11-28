from __future__ import annotations

import logging
from typing import Any

from acp.schema import (
    ElicitationMultiSelectPropertySchema,
    ElicitationSchema,
    ElicitationStringPropertySchema,
    StringMultiSelectItems,
)
import pytest

from acpterm.elicitation import render_form


def _schema(
    properties: dict[str, Any], required: list[str] | None = None
) -> ElicitationSchema:
    return ElicitationSchema(type="object", properties=properties, required=required)


def _string_schema(**kwargs: Any) -> ElicitationStringPropertySchema:
    return ElicitationStringPropertySchema(type="string", **kwargs)


def _multi_select_schema(
    *, default: list[str] | None = None
) -> ElicitationMultiSelectPropertySchema:
    return ElicitationMultiSelectPropertySchema(
        type="array",
        default=default,
        items=StringMultiSelectItems(type="string", enum=["cli", "backend"]),
    )


def _patch_ask(
    monkeypatch: pytest.MonkeyPatch, prompt_class: str, responses: list[Any]
) -> list[str]:
    """Replace a Rich prompt with a scripted queue and record each prompt."""
    queue = list(responses)
    calls: list[str] = []

    def fake_ask(prompt: str, **_kwargs: Any) -> Any:
        calls.append(prompt)
        if not queue:
            raise AssertionError("Prompt called more times than scripted")
        return queue.pop(0)

    monkeypatch.setattr(f"acpterm.elicitation.{prompt_class}.ask", fake_ask)
    return calls


def test_optional_select_returns_none_on_first_enter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_ask(monkeypatch, "IntPrompt", [None])
    schema = _schema({"colour": _string_schema(enum=["red", "blue"])})

    assert render_form("Pick a colour", schema) == {}
    assert len(calls) == 1


@pytest.mark.parametrize("bad_input", ["", "   ", ",,,"])
def test_required_multi_select_rejects_empty_input(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], bad_input: str
) -> None:
    calls = _patch_ask(monkeypatch, "Prompt", [bad_input, "2"])
    schema = _schema({"tags": _multi_select_schema()}, required=["tags"])

    assert render_form("Pick tags", schema) == {"tags": ["backend"]}
    assert len(calls) == 2
    assert capsys.readouterr().out.count("This field is required") == 1


def test_required_multi_select_exhaustion_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_ask(monkeypatch, "Prompt", ["", "", ""])
    schema = _schema({"tags": _multi_select_schema(default=["cli"])}, required=["tags"])

    assert render_form("Pick tags", schema) is None
    assert len(calls) == 3


@pytest.mark.parametrize(
    ("default", "required", "expected"),
    [
        (None, True, None),
        ([], True, None),
        (["cli"], True, {"tags": ["cli"]}),
        (None, False, {"tags": []}),
    ],
)
def test_multi_select_auto_accept(
    monkeypatch: pytest.MonkeyPatch,
    default: list[str] | None,
    required: bool,
    expected: dict[str, list[str]] | None,
) -> None:
    # An empty queue asserts that no prompt is issued during auto-accept.
    _patch_ask(monkeypatch, "Prompt", [])
    schema = _schema(
        {"tags": _multi_select_schema(default=default)},
        required=["tags"] if required else [],
    )

    assert render_form("Pick tags", schema, auto_accept=True) == expected


@pytest.mark.parametrize(
    ("responses", "expected", "expected_calls"),
    [
        (["abc123"], "abc123", 1),
        (["abcdef", "abc123"], "abc123", 2),
    ],
)
def test_pattern_search_matches_unanchored_substrings(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[str],
    expected: str,
    expected_calls: int,
) -> None:
    calls = _patch_ask(monkeypatch, "Prompt", responses)
    schema = _schema({"code": _string_schema(pattern=r"\d+")})

    assert render_form("Enter a code", schema) == {"code": expected}
    assert len(calls) == expected_calls


def test_invalid_pattern_logs_warning_and_accepts_value(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="acpterm.elicitation")
    _patch_ask(monkeypatch, "Prompt", ["abc"])
    schema = _schema({"code": _string_schema(pattern="[unclosed", min_length=3)})

    assert render_form("Enter a code", schema) == {"code": "abc"}
    assert "Invalid regex pattern" in caplog.text


def test_invalid_pattern_still_enforces_length_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_ask(monkeypatch, "Prompt", ["abc", "abcdef"])
    schema = _schema({"code": _string_schema(pattern="[unclosed", min_length=5)})

    assert render_form("Enter a code", schema) == {"code": "abcdef"}
    assert len(calls) == 2


def test_required_string_retry_exhaustion_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_ask(monkeypatch, "Prompt", ["abc", "abc", "abc"])
    schema = _schema(
        {"code": _string_schema(pattern=r"\d+", default="fallback")},
        required=["code"],
    )

    assert render_form("Enter a code", schema) is None
    assert len(calls) == 3


def test_optional_string_retry_exhaustion_returns_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_ask(monkeypatch, "Prompt", ["abc", "abc", "abc"])
    schema = _schema({"code": _string_schema(min_length=5, default="fallback")})

    assert render_form("Enter a code", schema) == {"code": "fallback"}
    assert len(calls) == 3


def test_required_select_retry_exhaustion_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_ask(monkeypatch, "IntPrompt", [None, None, None])
    schema = _schema(
        {"colour": _string_schema(enum=["red", "blue"], default="red")},
        required=["colour"],
    )

    assert render_form("Pick a colour", schema) is None
    assert len(calls) == 3
