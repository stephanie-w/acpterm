"""Shared token-usage validation and formatting helpers.

These helpers are used by both terminal rendering (``output``) and Markdown
transcripts (``transcript``) so that token totals and breakdowns stay identical.
"""

from __future__ import annotations

from typing import Any


_TOKEN_BREAKDOWN: tuple[tuple[str, str], ...] = (
    ("input_tokens", "input"),
    ("output_tokens", "output"),
    ("thought_tokens", "thought"),
)


def token_count(value: Any) -> int | None:
    """Return a valid non-negative token count, or ``None`` for malformed input."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def format_token_breakdown(usage: dict[str, Any]) -> tuple[int | None, str | None]:
    """Return the total token count and a formatted breakdown label.

    ``total_tokens`` is used when valid; otherwise it is derived from the valid
    breakdown components. ``breakdown`` is ``None`` when no component is valid.
    """
    total = token_count(usage.get("total_tokens"))
    parts: list[str] = []
    component_sum = 0
    for key, label in _TOKEN_BREAKDOWN:
        count = token_count(usage.get(key))
        if count is not None:
            parts.append(f"{label}: {count:,}")
            component_sum += count
    if total is None and parts:
        total = component_sum
    return total, (", ".join(parts) if parts else None)


def format_context_usage(usage: dict[str, Any]) -> str | None:
    """Return a ``used / size tokens (pct)`` summary, or ``None`` if unavailable."""
    used = token_count(usage.get("used"))
    size = token_count(usage.get("size"))
    if used is None or size is None:
        return None
    pct = (used / size * 100) if size > 0 else 0
    return f"{used:,} / {size:,} tokens ({pct:.1f}%)"


def format_cost(usage: dict[str, Any]) -> str | None:
    """Return a ``$amount currency`` summary, or ``None`` if unavailable."""
    cost = usage.get("cost")
    if cost is None:
        return None
    amount = getattr(cost, "amount", None)
    currency = getattr(cost, "currency", None)
    if amount is None or not currency:
        return None
    return f"${amount:.2f} {currency}"
