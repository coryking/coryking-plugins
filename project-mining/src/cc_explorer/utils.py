"""Shared formatting utilities for cc-explorer output."""

from datetime import datetime
from typing import Optional


def format_timestamp(ts: Optional[datetime]) -> str:
    """Format a datetime for display. Returns '-' for None."""
    if ts is None:
        return "-"
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def iso_timestamp(ts: Optional[datetime]) -> str:
    """Format a datetime as ISO 8601. Returns empty string for None."""
    if ts is None:
        return ""
    return ts.isoformat()


def short_uuid(uuid: str) -> str:
    """First 8 chars of a UUID for display."""
    return uuid[:8] if uuid else "--------"


def format_tokens(n: int) -> str:
    """Human-readable token count (e.g. '42K', '1.1M'). 0 → '-'."""
    if n == 0:
        return "-"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n // 1000}K"
    return str(n)


def format_duration(ms: Optional[int]) -> str:
    """Human-readable duration from milliseconds. None → '-'."""
    if ms is None:
        return "-"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}m{secs:.0f}s"
