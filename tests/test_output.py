from __future__ import annotations

from io import StringIO
from typing import Any

from acp import schema as acp_schema
import pytest
from rich.console import Console

from acpterm import output


@pytest.fixture(autouse=True)
def _reset_state() -> Any:
    output._state._usage = None
    output._state._mode_displayed = False
    output._state._model_displayed = False
    yield
    output._state._usage = None
    output._state._mode_displayed = False
    output._state._model_displayed = False


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
    output.format_session_update(
        "session",
        acp_schema.UsageUpdate(
            used=100,
            size=1000,
            cost=acp_schema.Cost(amount=0.42, currency="USD"),
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


@pytest.mark.parametrize("event_type", ["tool_call", "tool_call_update"])
def test_format_session_update_tool_call_name(
    rendered: StringIO, event_type: str
) -> None:
    if event_type == "tool_call":
        update = acp_schema.ToolCallStart(
            tool_call_id="tc-1",
            title="Run shell",
            name="bash",
            kind="execute",
            status="in_progress",
        )
    else:
        update = acp_schema.ToolCallProgress(
            tool_call_id="tc-1",
            title="Run shell",
            name="bash",
            kind="execute",
            status="in_progress",
        )
    output.format_session_update("session", update)

    text = rendered.getvalue()
    assert "Run shell" in text
    assert "[bash]" in text


@pytest.mark.parametrize("name", [None, ""])
@pytest.mark.parametrize("event_type", ["tool_call", "tool_call_update"])
def test_format_session_update_tool_call_omits_empty_name(
    rendered: StringIO, event_type: str, name: str | None
) -> None:
    if event_type == "tool_call":
        update = acp_schema.ToolCallStart(
            tool_call_id="tc-1",
            title="Run shell",
            name=name,
            kind="execute",
            status="in_progress",
        )
    else:
        update = acp_schema.ToolCallProgress(
            tool_call_id="tc-1",
            title="Run shell",
            name=name,
            kind="execute",
            status="in_progress",
        )
    output.format_session_update("session", update)

    text = rendered.getvalue()
    assert "Run shell" in text
    assert "[]" not in text
    assert "[None]" not in text


@pytest.mark.parametrize("event_type", ["tool_call", "tool_call_update"])
def test_format_session_update_escapes_tool_call_markup(
    rendered: StringIO, event_type: str
) -> None:
    if event_type == "tool_call":
        update = acp_schema.ToolCallStart(
            tool_call_id="tc-1",
            title="Run [red]shell[/red]",
            name="[bold]bash[/bold]",
            kind="execute",
            status="in_progress",
        )
    else:
        update = acp_schema.ToolCallProgress(
            tool_call_id="tc-1",
            title="Run [red]shell[/red]",
            name="[bold]bash[/bold]",
            kind="execute",
            status="in_progress",
        )
    output.format_session_update("session", update)

    text = rendered.getvalue()
    assert "Run [red]shell[/red]" in text
    assert "[bold]bash[/bold]" in text


def test_format_session_update_reads_current_mode_from_canonical_field(
    rendered: StringIO,
) -> None:
    output.format_session_update(
        "session", acp_schema.CurrentModeUpdate(current_mode_id="build")
    )

    assert "[mode] build" in rendered.getvalue()


def test_format_session_update_reads_model_config_option(
    rendered: StringIO,
) -> None:
    output.format_session_update(
        "session",
        acp_schema.ConfigOptionUpdate(
            config_options=[
                acp_schema.SessionConfigOptionSelect(
                    id="model",
                    name="Model",
                    current_value="gpt-5",
                    options=[
                        acp_schema.SessionConfigSelectOption(
                            value="gpt-5", name="GPT-5"
                        )
                    ],
                )
            ]
        ),
    )

    assert "[model] gpt-5" in rendered.getvalue()


def test_format_session_update_renders_terminal_content(rendered: StringIO) -> None:
    output.format_session_update(
        "session",
        acp_schema.ToolCallProgress(
            tool_call_id="tc-1",
            content=[acp_schema.TerminalToolCallContent(terminal_id="term-1")],
        ),
    )

    assert "[terminal term-1]" in rendered.getvalue()


def test_display_initial_session_info_reads_modes_and_config_options(
    rendered: StringIO,
) -> None:
    response = acp_schema.NewSessionResponse(
        session_id="session-1",
        modes=acp_schema.SessionModeState(
            current_mode_id="build",
            available_modes=[acp_schema.SessionMode(id="build", name="Build")],
        ),
        config_options=[
            acp_schema.SessionConfigOptionSelect(
                id="model",
                name="Model",
                current_value="gpt-5",
                options=[
                    acp_schema.SessionConfigSelectOption(value="gpt-5", name="GPT-5")
                ],
            )
        ],
    )

    output.display_initial_session_info(response)

    text = rendered.getvalue()
    assert "[mode] build" in text
    assert "[model] gpt-5" in text


def test_display_initial_session_info_reads_field_meta_model_fallback(
    rendered: StringIO,
) -> None:
    response = acp_schema.NewSessionResponse(
        session_id="session-1",
        field_meta={"opencode": {"modelId": "opencode-default"}},
    )

    output.display_initial_session_info(response)

    assert "[model] opencode-default" in rendered.getvalue()


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
