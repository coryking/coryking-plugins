"""Shared formatting utilities for cc-explorer output."""


def short_uuid(uuid: str) -> str:
    """First 8 chars of a UUID for display."""
    return uuid[:8] if uuid else "--------"
