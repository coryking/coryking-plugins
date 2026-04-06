"""Shared formatting utilities for cc-explorer output."""

from __future__ import annotations

import textwrap
from typing import Any


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



class PrefixId(str):
    """UUID value object with prefix matching and short display.

    str subclass — drop-in wherever str is used. Stores the full value
    internally but displays/serializes as the first 8 characters.

    Equality: if either side is shorter than a full UUID (36 chars),
    does prefix matching via startswith. Both full → exact match.

    Hash: based on first 8 chars so dict[PrefixId] lookups work with
    both full UUIDs and short prefixes.
    """

    def __new__(cls, value: str = "") -> PrefixId:
        # Explicit signature so pyright sees PrefixId(str) -> PrefixId
        # rather than inheriting str.__new__'s return type.
        return super().__new__(cls, value)

    @property
    def full(self) -> str:
        """The complete value as originally provided."""
        return super().__str__()

    @property
    def short(self) -> str:
        """First 8 chars for display."""
        val = self.full
        return val[:8] if val else "--------"

    @property
    def is_prefix(self) -> bool:
        """True if this value is shorter than a full UUID."""
        return len(self.full) < 36

    def __str__(self) -> str:
        return self.short

    def __repr__(self) -> str:
        return f"PrefixId({self.full!r})"

    def __format__(self, format_spec: str) -> str:
        return format(self.short, format_spec)

    def __eq__(self, other) -> bool:
        if not isinstance(other, str):
            return NotImplemented
        a = self.full
        b = other.full if isinstance(other, PrefixId) else other
        if len(a) < 36 or len(b) < 36:
            short, long = (a, b) if len(a) <= len(b) else (b, a)
            return long.startswith(short)
        return a == b

    def __ne__(self, other) -> bool:
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def __hash__(self) -> int:
        return hash(self.full[:8])

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> Any:
        from pydantic_core import core_schema

        return core_schema.no_info_plain_validator_function(
            cls._pydantic_validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda v: v.short if isinstance(v, PrefixId) else str(v)[:8],
                info_arg=False,
            ),
        )

    @classmethod
    def __get_pydantic_json_schema__(cls, schema: Any, handler: Any) -> dict:
        return {"type": "string", "description": "UUID or UUID prefix (8+ chars)."}

    @classmethod
    def _pydantic_validate(cls, value: Any) -> PrefixId:
        if isinstance(value, PrefixId):
            return value
        if isinstance(value, str):
            return cls(value)
        raise ValueError(f"Expected str or PrefixId, got {type(value)}")
