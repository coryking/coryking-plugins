"""Transcript harness providers.

Providers own on-disk discovery and wire-format parsing.  The search layer sees
one normalized transcript model regardless of which harness wrote the files.
"""

from .base import Harness, TranscriptProvider


def parse_harnesses(values: list[str] | None) -> set[Harness]:
    """None/empty means every supported harness."""
    if not values:
        return set(Harness)
    try:
        return {Harness(value) for value in values}
    except ValueError as exc:
        valid = ", ".join(h.value for h in Harness)
        raise ValueError(f"Unknown harness {exc.args[0]!r}; valid harnesses: {valid}") from exc


def providers_for(values: list[str] | None = None) -> list[TranscriptProvider]:
    """Fresh provider instances for the requested harness set."""
    from .claude import ClaudeProvider
    from .codex import CodexProvider

    enabled = parse_harnesses(values)
    providers: list[TranscriptProvider] = []
    if Harness.claude in enabled:
        providers.append(ClaudeProvider())
    if Harness.codex in enabled:
        providers.append(CodexProvider())
    return providers


def provider_for(harness: Harness) -> TranscriptProvider:
    return next(provider for provider in providers_for([harness.value]))
