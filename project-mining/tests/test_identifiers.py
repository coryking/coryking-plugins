"""Legacy references are queries; stored identifiers retain ordinary string semantics."""

import pytest
from cc_explorer.identifiers import id_matches, matching_ids


@pytest.mark.parametrize(
    "query,expected",
    [
        ("", False),
        ("a", False),
        ("abcdef", True),
        ("abcdef12", True),
        ("abcdef12-other", False),
    ],
)
def test_explicit_prefix_floor(query, expected):
    assert id_matches("abcdef12-full", query) is expected


def test_exact_short_identifier_wins():
    ids = ["abcdef12-full", "abcdef12"]
    assert matching_ids(ids, "abcdef12", lambda value: (value,)) == ["abcdef12"]
    assert id_matches("abc", "abc")


def test_collisions_are_preserved_for_resolver():
    ids = ["abcdef12-first", "abcdef12-second"]
    assert matching_ids(ids, "abcdef12", lambda value: (value,)) == ids
    assert len(set(ids)) == 2
