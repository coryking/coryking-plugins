"""Tests for PrefixId — UUID value object with prefix matching.

Defines the behavioral contract:
- str subclass (drop-in compatible)
- Exact equality for full UUIDs (36 chars)
- Prefix matching when either side is short
- != is the inverse of == (not inherited from str's C-level __ne__)
- Hash on first 8 chars (so dicts work with prefix lookups)
- 'in' searches the full UUID (the identity), not the short display form
- .short property for display (first 8 chars)
- str() / f-string returns short form (MCP-boundary friendly)
- .full property for the complete value
- Works as dict key with prefix lookups
"""

import pytest

from cc_explorer.utils import PrefixId

# Two full UUIDs that share an 8-char prefix would be astronomically unlikely,
# so our test fixtures use distinct prefixes.
FULL_A = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
FULL_B = PrefixId("b1234567-aaaa-bbbb-cccc-dddddddddddd")
SHORT_A = PrefixId("a9529cc1")
SHORT_B = PrefixId("b1234567")


# =============================================================================
# Construction and str compatibility
# =============================================================================


class TestConstruction:
    def test_from_full_uuid(self):
        pid = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
        assert isinstance(pid, str)
        assert isinstance(pid, PrefixId)

    def test_from_short_prefix(self):
        pid = PrefixId("a9529cc1")
        assert isinstance(pid, str)
        assert isinstance(pid, PrefixId)

    def test_from_empty(self):
        pid = PrefixId("")
        assert pid == ""

    def test_str_returns_short_form(self):
        """str() returns the short form — this is what Pydantic serializes, what agents see."""
        pid = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
        assert str(pid) == "a9529cc1"

    def test_str_of_short_is_identity(self):
        """str() of an already-short value is itself."""
        pid = PrefixId("a9529cc1")
        assert str(pid) == "a9529cc1"

    def test_works_in_fstring(self):
        """f-strings use __format__ which should also give the short form."""
        pid = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
        assert f"id={pid}" == "id=a9529cc1"

    def test_full_property(self):
        """.full gives back the complete value that was passed in."""
        full = "a9529cc1-b576-5fd3-9f1a-1234567890ab"
        pid = PrefixId(full)
        assert pid.full == full

    def test_full_of_short(self):
        """.full on a prefix just returns the prefix (it's all we have)."""
        pid = PrefixId("a9529cc1")
        assert pid.full == "a9529cc1"

    def test_is_prefix_for_short(self):
        pid = PrefixId("a9529cc1")
        assert pid.is_prefix is True

    def test_is_prefix_for_full(self):
        pid = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
        assert pid.is_prefix is False

    def test_is_prefix_for_empty(self):
        pid = PrefixId("")
        assert pid.is_prefix is True


# =============================================================================
# .short property
# =============================================================================


class TestShort:
    def test_short_of_full_uuid(self):
        pid = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
        assert pid.short == "a9529cc1"

    def test_short_of_prefix(self):
        """If the value is already 8 chars, .short is identity."""
        pid = PrefixId("a9529cc1")
        assert pid.short == "a9529cc1"

    def test_short_of_empty(self):
        pid = PrefixId("")
        assert pid.short == "--------"

    def test_short_of_shorter_than_8(self):
        pid = PrefixId("abc")
        assert pid.short == "abc"


# =============================================================================
# Equality and inequality — the core behavior
#
# PrefixId inherits from str (a C builtin). str's __ne__ is its own C-level
# method that does exact byte comparison — it does NOT delegate to __eq__.
# So overriding __eq__ without __ne__ silently breaks != for prefix matches.
# Both operators must be tested independently.
# =============================================================================


class TestEquality:
    # --- __eq__ (==) ---

    def test_full_vs_full_same(self):
        a = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
        b = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
        assert a == b

    def test_full_vs_full_different(self):
        assert not (FULL_A == FULL_B)

    def test_short_matches_full(self):
        """A prefix matches a full UUID that starts with it."""
        assert SHORT_A == FULL_A

    def test_full_matches_short(self):
        """Symmetric — order shouldn't matter."""
        assert FULL_A == SHORT_A

    def test_short_vs_short_same(self):
        a = PrefixId("a9529cc1")
        b = PrefixId("a9529cc1")
        assert a == b

    def test_short_vs_short_different(self):
        assert not (SHORT_A == SHORT_B)

    def test_vs_plain_str_prefix(self):
        """PrefixId should work with plain str on the other side."""
        assert FULL_A == "a9529cc1"

    def test_vs_plain_str_full(self):
        assert FULL_A == "a9529cc1-b576-5fd3-9f1a-1234567890ab"

    def test_vs_non_string(self):
        assert not (SHORT_A == 42)
        assert not (SHORT_A == None)  # noqa: E711

    def test_very_short_prefix(self):
        """Even a 1-char prefix should match."""
        assert FULL_A == PrefixId("a")
        assert not (FULL_A == PrefixId("b"))

    # --- __ne__ (!=) — must be the inverse of __eq__ ---

    def test_ne_prefix_match_is_false(self):
        """!= must return False when __eq__ returns True (prefix match)."""
        assert not (FULL_A != SHORT_A)

    def test_ne_plain_str_prefix_is_false(self):
        assert not (FULL_A != "a9529cc1")

    def test_ne_same_full_is_false(self):
        a = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
        b = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
        assert not (a != b)

    def test_ne_different_is_true(self):
        assert FULL_A != FULL_B

    def test_ne_no_match_plain_str(self):
        assert FULL_A != "xxxxxxxx"

    def test_ne_non_string(self):
        assert SHORT_A != 42


# =============================================================================
# Containment — 'in' searches the full UUID (the identity)
#
# str.__contains__ already operates on the raw internal value, which is the
# full UUID. This is the behavior we want: the full UUID is the identity,
# the short form is just display. These tests pin that behavior.
# =============================================================================


class TestContains:
    def test_prefix_in_full(self):
        assert "a9529cc1" in FULL_A

    def test_middle_fragment_in_full(self):
        assert "b576" in FULL_A

    def test_end_fragment_in_full(self):
        assert "890ab" in FULL_A

    def test_nonexistent_fragment(self):
        assert "zzzzz" not in FULL_A

    def test_contains_on_short_prefix_id(self):
        """A PrefixId created from a short value only has that much to search."""
        assert "a952" in SHORT_A
        assert "b576" not in SHORT_A


# =============================================================================
# Hashing — must be consistent with equality
# =============================================================================


class TestHashing:
    def test_full_and_prefix_hash_equal(self):
        """If a == b, then hash(a) == hash(b). Required by Python data model."""
        assert hash(FULL_A) == hash(SHORT_A)

    def test_same_full_uuids_hash_equal(self):
        a = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
        b = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
        assert hash(a) == hash(b)

    def test_different_prefixes_hash_different(self):
        """Different 8-char prefixes should (almost certainly) hash differently."""
        assert hash(SHORT_A) != hash(SHORT_B)

    def test_usable_in_set(self):
        s = {FULL_A}
        assert SHORT_A in s

    def test_prefix_in_set_finds_full(self):
        """A set containing a full UUID should match a prefix lookup."""
        s = {FULL_A}
        assert SHORT_A in s


# =============================================================================
# Dict key behavior — the practical payoff
# =============================================================================


class TestDictKeys:
    def test_store_full_lookup_by_prefix(self):
        """Store with full UUID key, retrieve with prefix."""
        d = {FULL_A: "found it"}
        assert d[SHORT_A] == "found it"

    def test_store_full_lookup_by_full(self):
        d = {FULL_A: "found it"}
        assert d[FULL_A] == "found it"

    def test_prefix_in_dict(self):
        d = {FULL_A: "val"}
        assert SHORT_A in d

    def test_plain_str_prefix_in_dict(self):
        """Plain str DOES match — PrefixId.__eq__ handles the comparison
        during bucket probing even though hashes differ.

        This means you can do d["a9529cc1"] on a PrefixId-keyed dict.
        """
        d = {FULL_A: "val"}
        assert "a9529cc1" in d

    def test_multiple_keys_distinct(self):
        d = {FULL_A: "A", FULL_B: "B"}
        assert d[SHORT_A] == "A"
        assert d[SHORT_B] == "B"


# =============================================================================
# Repr
# =============================================================================


class TestRepr:
    def test_repr_full(self):
        r = repr(FULL_A)
        assert "PrefixId" in r
        assert "a9529cc1" in r

    def test_repr_short(self):
        r = repr(SHORT_A)
        assert "PrefixId" in r
        assert "a9529cc1" in r
