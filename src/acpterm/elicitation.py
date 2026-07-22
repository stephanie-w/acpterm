"""Terminal form renderer for ACP elicitation requests.

Renders structured form schemas as interactive terminal prompts using ``rich``.
Supports string (with enum/oneOf selection), integer, number, boolean,
and multi-select (array) field types.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from acp import schema as acp_schema
from rich.console import Console
from rich.prompt import Confirm, FloatPrompt, IntPrompt, Prompt


_console = Console(highlight=False)
_log = logging.getLogger(__name__)

MAX_RETRIES = 3


def _tag(label: str, style: str | None = None) -> str:
    """Format a bracketed tag for Rich output."""
    escaped = f"\\[{label}]"
    if style:
        return f"[{style}]{escaped}[/{style}]"
    return escaped


def render_form(
    message: str,
    schema: acp_schema.ElicitationSchema,
    auto_accept: bool = False,
) -> dict[str, Any] | None:
    """Render an elicitation form in the terminal.

    Args:
        message: Display message from the agent.
        schema: The form schema defining fields to collect.
        auto_accept: If True, use default values without prompting.

    Returns:
        Dictionary of field name to value, or None to decline.
    """
    _console.print(f"\n{_tag('elicit', 'bold cyan')} {message}")
    if schema.title:
        _console.print(f"  [bold]{schema.title}[/bold]")
    if schema.description:
        _console.print(f"  [dim]{schema.description}[/dim]")

    properties = schema.properties
    if not properties:
        return {}

    required_fields = set(schema.required or [])
    result: dict[str, Any] = {}

    for field_name, field_schema in properties.items():
        is_required = field_name in required_fields
        value = _render_field(field_name, field_schema, is_required, auto_accept)

        if value is not None:
            result[field_name] = value
        elif is_required:
            if auto_accept:
                _console.print(
                    f"  [dim]Cannot auto-accept: required field"
                    f" '{field_name}' has no default[/dim]"
                )
            return None

    return result


def _field_label(name: str, schema: Any, required: bool) -> str:
    """Build a Rich-formatted label for a form field."""
    title = getattr(schema, "title", None) or name
    suffix = " [dim](required)[/dim]" if required else ""
    description = getattr(schema, "description", None)
    label = f"  [bold]{title}{suffix}[/bold]"
    if description:
        label += f"\n  [dim]{description}[/dim]"
    return label


def _render_field(
    name: str,
    schema: Any,
    required: bool,
    auto_accept: bool,
) -> Any:
    """Dispatch to the correct renderer based on field type."""
    field_type = getattr(schema, "type", None)

    renderers: dict[str, Any] = {
        "string": _render_string,
        "integer": _render_integer,
        "number": _render_number,
        "boolean": _render_boolean,
        "array": _render_multi_select,
    }

    renderer = renderers.get(field_type)
    if renderer is None:
        _console.print(f"  [dim]Skipping unsupported field type: {field_type}[/dim]")
        return None

    return renderer(name, schema, required, auto_accept)


# ── String ────────────────────────────────────────────────────────────────────


def _render_string(
    name: str, schema: Any, required: bool, auto_accept: bool
) -> str | None:
    """Render a string field — free-form or enum/oneOf selection."""
    default: str | None = getattr(schema, "default", None)
    enum_values: list[str] | None = getattr(schema, "enum", None)
    one_of: list[Any] | None = getattr(schema, "one_of", None)

    if enum_values or one_of:
        return _render_select(name, schema, required, auto_accept, enum_values, one_of)

    if auto_accept:
        return default

    _console.print(_field_label(name, schema, required))

    min_length: int | None = getattr(schema, "min_length", None)
    max_length: int | None = getattr(schema, "max_length", None)
    pattern: str | None = getattr(schema, "pattern", None)

    for _ in range(MAX_RETRIES):
        value = Prompt.ask(f"  {name}", default=default, console=_console)
        if not value and not required:
            return default
        if not value and required:
            _console.print("  [red]This field is required[/red]")
            continue
        if value and min_length and len(value) < min_length:
            _console.print(f"  [red]Minimum length: {min_length}[/red]")
            continue
        if value and max_length and len(value) > max_length:
            _console.print(f"  [red]Maximum length: {max_length}[/red]")
            continue
        if value and pattern and not re.match(pattern, value):
            _console.print(f"  [red]Must match pattern: {pattern}[/red]")
            continue
        return value

    return default


def _render_select(
    name: str,
    schema: Any,
    required: bool,
    auto_accept: bool,
    enum_values: list[str] | None,
    one_of: list[Any] | None,
) -> str | None:
    """Render a string field with enumerated choices as a numbered menu."""
    default: str | None = getattr(schema, "default", None)

    options: list[tuple[str, str]] = []
    if one_of:
        for opt in one_of:
            opt_value = (
                getattr(opt, "const", None) or getattr(opt, "value", None) or str(opt)
            )
            opt_title = (
                getattr(opt, "title", None) or getattr(opt, "label", None) or opt_value
            )
            options.append((opt_value, opt_title))
    elif enum_values:
        options = [(v, v) for v in enum_values]

    if not options:
        return default

    if auto_accept:
        return default if default else options[0][0]

    _console.print(_field_label(name, schema, required))

    default_idx: int | None = None
    for i, (value, label) in enumerate(options, 1):
        marker = " ★" if value == default else ""
        _console.print(f"    {i}. {label}{marker}")
        if value == default:
            default_idx = i

    for _ in range(MAX_RETRIES):
        choice = IntPrompt.ask(
            f"  Select [1-{len(options)}]",
            default=default_idx,
            console=_console,
        )
        if choice is not None and 1 <= choice <= len(options):
            return options[choice - 1][0]
        _console.print(
            f"  [red]Please enter a number between 1 and {len(options)}[/red]"
        )

    return default


# ── Integer ───────────────────────────────────────────────────────────────────


def _render_integer(
    name: str, schema: Any, required: bool, auto_accept: bool
) -> int | None:
    """Render an integer field with optional min/max bounds."""
    default: int | None = getattr(schema, "default", None)

    if auto_accept:
        return default

    _console.print(_field_label(name, schema, required))

    minimum: int | None = getattr(schema, "minimum", None)
    maximum: int | None = getattr(schema, "maximum", None)

    if minimum is not None and maximum is not None:
        hint = f"  {name} [{minimum}-{maximum}]"
    elif minimum is not None:
        hint = f"  {name} [≥{minimum}]"
    elif maximum is not None:
        hint = f"  {name} [≤{maximum}]"
    else:
        hint = f"  {name}"

    for _ in range(MAX_RETRIES):
        value = IntPrompt.ask(hint, default=default, console=_console)
        if value is None and not required:
            return default
        if value is not None:
            if minimum is not None and value < minimum:
                _console.print(f"  [red]Minimum value: {minimum}[/red]")
                continue
            if maximum is not None and value > maximum:
                _console.print(f"  [red]Maximum value: {maximum}[/red]")
                continue
            return value

    return default


# ── Number (float) ────────────────────────────────────────────────────────────


def _render_number(
    name: str, schema: Any, required: bool, auto_accept: bool
) -> float | None:
    """Render a number (float) field with optional min/max bounds."""
    default: float | None = getattr(schema, "default", None)

    if auto_accept:
        return default

    _console.print(_field_label(name, schema, required))

    minimum: float | None = getattr(schema, "minimum", None)
    maximum: float | None = getattr(schema, "maximum", None)

    if minimum is not None and maximum is not None:
        hint = f"  {name} [{minimum}-{maximum}]"
    elif minimum is not None:
        hint = f"  {name} [≥{minimum}]"
    elif maximum is not None:
        hint = f"  {name} [≤{maximum}]"
    else:
        hint = f"  {name}"

    for _ in range(MAX_RETRIES):
        value = FloatPrompt.ask(hint, default=default, console=_console)
        if value is None and not required:
            return default
        if value is not None:
            if minimum is not None and value < minimum:
                _console.print(f"  [red]Minimum value: {minimum}[/red]")
                continue
            if maximum is not None and value > maximum:
                _console.print(f"  [red]Maximum value: {maximum}[/red]")
                continue
            return value

    return default


# ── Boolean ───────────────────────────────────────────────────────────────────


def _render_boolean(
    name: str, schema: Any, required: bool, auto_accept: bool
) -> bool | None:
    """Render a boolean field as a yes/no confirmation."""
    default: bool | None = getattr(schema, "default", None)

    if auto_accept:
        return default if default is not None else True

    _console.print(_field_label(name, schema, required))
    return Confirm.ask(
        f"  {name}",
        default=default if default is not None else True,
        console=_console,
    )


# ── Multi-select (array) ─────────────────────────────────────────────────────


def _render_multi_select(
    name: str, schema: Any, required: bool, auto_accept: bool
) -> list[str] | None:
    """Render a multi-select field as a numbered checklist."""
    default: list[str] | None = getattr(schema, "default", None)
    items = getattr(schema, "items", None)

    options: list[tuple[str, str]] = []
    if items:
        one_of = getattr(items, "one_of", None)
        enum_values = getattr(items, "enum", None)
        if one_of:
            for opt in one_of:
                opt_value = (
                    getattr(opt, "const", None)
                    or getattr(opt, "value", None)
                    or str(opt)
                )
                opt_label = (
                    getattr(opt, "title", None)
                    or getattr(opt, "label", None)
                    or opt_value
                )
                options.append((opt_value, opt_label))
        elif enum_values:
            options = [(v, v) for v in enum_values]

    if not options:
        return default

    if auto_accept:
        return default or []

    _console.print(_field_label(name, schema, required))

    default_set = set(default or [])
    for i, (value, label) in enumerate(options, 1):
        marker = " ★" if value in default_set else ""
        _console.print(f"    {i}. {label}{marker}")

    min_items: int | None = getattr(schema, "min_items", None)
    max_items: int | None = getattr(schema, "max_items", None)

    _console.print("  [dim]Enter numbers separated by commas (e.g. 1,3,4)[/dim]")

    for _ in range(MAX_RETRIES):
        raw = Prompt.ask("  Select", console=_console)
        if not raw and not required:
            return default or []

        try:
            indices = [int(x.strip()) for x in raw.split(",") if x.strip()]
        except ValueError:
            _console.print("  [red]Enter numbers separated by commas[/red]")
            continue

        if any(i < 1 or i > len(options) for i in indices):
            _console.print(f"  [red]Numbers must be between 1 and {len(options)}[/red]")
            continue

        selected = [options[i - 1][0] for i in indices]

        if min_items is not None and len(selected) < min_items:
            _console.print(f"  [red]Select at least {min_items} item(s)[/red]")
            continue
        if max_items is not None and len(selected) > max_items:
            _console.print(f"  [red]Select at most {max_items} item(s)[/red]")
            continue

        return selected

    return default or []
