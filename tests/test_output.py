from __future__ import annotations

from io import StringIO
from types import SimpleNamespace
from typing import Any

import pytest
from rich.console import Console

from acpterm import output


@pytest.fixture(autouse=True)
def _reset_state() -> Any:
    output._state._usage = None
    yield
    output._state._usage = None


@pytest.fixture
def rendered(monkeypatch: pytest.MonkeyPatch) -> StringIO:
    stream = StringIO()
    console = Console(
        file=stream,
        width=200,
        force_terminal=False,
        color_system=None,
        highlight=False,
    )
    monkeypatch.setattr(output, "_console", console)
    return stream


def test_format_stop_reason_renders_tokens(rendered: StringIO) -> None:
    output.format_stop_reason(
        "end_turn",
        usage={
            "total_tokens": 150,
            "input_tokens": 100,
            "output_tokens": 40,
            "thought_tokens": 10,
        },
    )

    text = rendered.getvalue()
    assert "[tokens] 150 tokens (input: 100, output: 40, thought: 10)" in text
    assert "[done] end_turn" in text


def test_format_stop_reason_derives_total_from_breakdown(rendered: StringIO) -> None:
    output.format_stop_reason(
        "end_turn",
        usage={"input_tokens": 100, "output_tokens": 40, "thought_tokens": 10},
    )

    assert "[tokens] 150 tokens (input: 100, output: 40, thought: 10)" in (
        rendered.getvalue()
    )


def test_format_stop_reason_renders_all_usage_types(rendered: StringIO) -> None:
    cost = SimpleNamespace(amount=0.42, currency="USD")
    output.format_session_update(
        "session",
        SimpleNamespace(
            session_update="usage_update",
            used=100,
            size=1000,
            cost=cost,
        ),
    )

    output.format_stop_reason("end_turn", usage={"total_tokens": 50})

    text = rendered.getvalue()
    assert (
        text.index("[tokens]")
        < text.index("[context]")
        < text.index("[cost]")
        < text.index("[done]")
    )
    assert "[context] 100 / 1,000 tokens" in text
    assert "[cost] $0.42 USD" in text


def test_print_usage_resets_cached_usage(rendered: StringIO) -> None:
    output._state._usage = {"used": 100, "size": 1000}

    output._print_usage()

    assert output._state._usage is None

    rendered.seek(0)
    rendered.truncate(0)
    output._print_usage()

    assert "[context]" not in rendered.getvalue()


@pytest.mark.parametrize(
    "bad",
    [True, False, "150", -1, float("nan"), float("inf"), float("-inf"), 1.5, None],
)
def test_format_stop_reason_ignores_malformed_tokens(
    rendered: StringIO, bad: Any
) -> None:
    output.format_stop_reason(
        "end_turn",
        usage={
            "total_tokens": bad,
            "input_tokens": bad,
            "output_tokens": bad,
            "thought_tokens": bad,
        },
    )

    text = rendered.getvalue()
    assert "[tokens]" not in text
    assert "[done] end_turn" in text


def test_format_stop_reason_ignores_only_malformed_tokens(rendered: StringIO) -> None:
    output.format_stop_reason(
        "end_turn",
        usage={
            "total_tokens": float("nan"),
            "input_tokens": 100,
            "output_tokens": "40",
            "thought_tokens": -10,
        },
    )

    assert "[tokens] 100 tokens (input: 100)" in rendered.getvalue()
