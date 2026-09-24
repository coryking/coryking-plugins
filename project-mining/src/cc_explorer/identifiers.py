"""Exact stored strings; explicit matching for legacy references at lookup boundaries."""

from collections.abc import Callable, Iterable
from typing import TypeVar

MIN_ID_LEN = 6
T = TypeVar("T")


def id_matches(stored: str | None, query: str) -> bool:
    """Nonempty exact values always match; prefixes require the common length floor.

    Short non-UUID ids can be complete identities. Length never decides whether
    a stored value is an identity or an abbreviation; only the lookup does.
    """
    if not stored or not query:
        return False
    return stored == query or (len(query) >= MIN_ID_LEN and stored.startswith(query))


def matching_ids(
    items: Iterable[T], query: str, ids: Callable[[T], Iterable[str]]
) -> list[T]:
    """Prefer exact identity over prefix matches, considering every candidate.

    `ids` may expose aliases for the same item (agent id and dispatch tool id).
    Callers decide whether multiple items represent distinct objects in their
    lookup scope; discovery must retain all prefix candidates until this point.
    """
    exact: list[T] = []
    prefixes: list[T] = []
    for item in items:
        values = tuple(ids(item))
        if query and query in values:
            exact.append(item)
        elif any(id_matches(value, query) for value in values):
            prefixes.append(item)
    return exact or prefixes


def ambiguous_id(query: str, kind: str, candidates: Iterable[str]) -> ValueError:
    """A complete repairable diagnostic, shared by typed and raw-file resolvers."""
    labels = sorted(set(candidates))
    return ValueError(
        f"{kind} reference {query!r} is ambiguous — {len(labels)} candidates:\n"
        + "\n".join(labels)
        + "\nUse a complete identifier or narrow the project/session scope. "
        "For a legacy citation, use its surrounding context to choose; do not guess."
    )
