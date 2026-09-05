"""Shared formatting utilities for cc-explorer output."""

from __future__ import annotations

import textwrap


def smart_truncate(text: str, width: int, placeholder: str = "...") -> str:
    """Truncate text to width, preferring word boundaries.

    width=0 means no truncation (return as-is).
    Tries textwrap.shorten first (word-boundary break). If that collapses
    to just the placeholder (single long token, no spaces), falls back to
    a hard character cut.
    """
    if not width or len(text) <= width:
        return text
    result = textwrap.shorten(text, width=width, placeholder=placeholder)
    if result != placeholder:
        return result
    # Fallback: hard cut (no word boundary found)
    return text[: width - len(placeholder)] + placeholder


# Whitespace collapse can only ever SHRINK a string, so bounding the input to a
# generous multiple of the wanted width is safe and keeps the cost proportional
# to the window rather than to the input.
_COLLAPSE_SLACK = 8


def collapse_ws(text: str, limit: int) -> str:
    """Whitespace-collapsed leading slice, bounded BEFORE it is normalized.

    `" ".join(text.split())[:limit]` normalizes the WHOLE string to read a
    `limit`-char window — on a 540 KB tool result that is ~96x slower than
    necessary, and tool results are exactly where huge strings live. Slicing
    first is equivalent for any input whose leading `limit * 8` chars are not
    overwhelmingly whitespace, and bounded regardless of input size.
    """
    return " ".join(text[: limit * _COLLAPSE_SLACK].split())[:limit]
