"""Tests for PrefixId — UUID value object with prefix matching.

Defines the behavioral contract:
- str subclass (drop-in compatible)
- Exact equality for full UUIDs (36 chars)
- Prefix matching when either side is short
- Hash on first 8 chars (so dicts work with prefix lookups)
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
# Equality — the core behavior
# =============================================================================


class TestEquality:
    def test_full_vs_full_same(self):
        a = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
        b = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
        assert a == b

    def test_full_vs_full_different(self):
        assert FULL_A != FULL_B

    def test_short_matches_full(self):
        """A prefix matches a full UUID that starts with it."""
        full = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
        short = PrefixId("a9529cc1")
        assert short == full

    def test_full_matches_short(self):
        """Symmetric — order shouldn't matter."""
        full = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
        short = PrefixId("a9529cc1")
        assert full == short

    def test_short_vs_short_same(self):
        a = PrefixId("a9529cc1")
        b = PrefixId("a9529cc1")
        assert a == b

    def test_short_vs_short_different(self):
        assert SHORT_A != SHORT_B

    def test_short_no_match(self):
        full = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
        short = PrefixId("b1234567")
        assert full != short

    def test_vs_plain_str_prefix(self):
        """PrefixId should work with plain str on the other side."""
        pid = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
        assert pid == "a9529cc1"

    def test_vs_plain_str_full(self):
        pid = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
        assert pid == "a9529cc1-b576-5fd3-9f1a-1234567890ab"

    def test_vs_plain_str_no_match(self):
        pid = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
        assert pid != "xxxxxxxx"

    def test_vs_non_string(self):
        pid = PrefixId("a9529cc1")
        assert pid != 42
        assert pid != None  # noqa: E711

    def test_very_short_prefix(self):
        """Even a 1-char prefix should match."""
        full = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
        assert full == PrefixId("a")
        assert full != PrefixId("b")


# =============================================================================
# Hashing — must be consistent with equality
# =============================================================================


class TestHashing:
    def test_full_and_prefix_hash_equal(self):
        """If a == b, then hash(a) == hash(b). Required by Python data model."""
        full = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
        short = PrefixId("a9529cc1")
        assert hash(full) == hash(short)

    def test_same_full_uuids_hash_equal(self):
        a = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
        b = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
        assert hash(a) == hash(b)

    def test_different_prefixes_hash_different(self):
        """Different 8-char prefixes should (almost certainly) hash differently."""
        assert hash(SHORT_A) != hash(SHORT_B)

    def test_usable_in_set(self):
        s = {PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")}
        assert PrefixId("a9529cc1") in s

    def test_prefix_in_set_finds_full(self):
        """A set containing a full UUID should match a prefix lookup."""
        s = {PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")}
        assert PrefixId("a9529cc1") in s


# =============================================================================
# Dict key behavior — the practical payoff
# =============================================================================


class TestDictKeys:
    def test_store_full_lookup_by_prefix(self):
        """Store with full UUID key, retrieve with prefix."""
        d = {PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab"): "found it"}
        assert d[PrefixId("a9529cc1")] == "found it"

    def test_store_full_lookup_by_full(self):
        d = {PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab"): "found it"}
        assert d[PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")] == "found it"

    def test_prefix_in_dict(self):
        d = {PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab"): "val"}
        assert PrefixId("a9529cc1") in d

    def test_plain_str_prefix_in_dict(self):
        """Plain str DOES match — PrefixId.__eq__ handles the comparison
        during bucket probing even though hashes differ.

        This means you can do d["a9529cc1"] on a PrefixId-keyed dict.
        """
        d = {PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab"): "val"}
        assert "a9529cc1" in d

    def test_multiple_keys_distinct(self):
        d = {
            PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab"): "A",
            PrefixId("b1234567-aaaa-bbbb-cccc-dddddddddddd"): "B",
        }
        assert d[PrefixId("a9529cc1")] == "A"
        assert d[PrefixId("b1234567")] == "B"


# =============================================================================
# Repr
# =============================================================================


class TestRepr:
    def test_repr_full(self):
        pid = PrefixId("a9529cc1-b576-5fd3-9f1a-1234567890ab")
        r = repr(pid)
        assert "PrefixId" in r
        assert "a9529cc1" in r

    def test_repr_short(self):
        pid = PrefixId("a9529cc1")
        r = repr(pid)
        assert "PrefixId" in r
        assert "a9529cc1" in r
