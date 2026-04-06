"""Tests for _match_example excerpt extraction.

Two bugs:
1. Greedy regex (e.g. comment_count.*0) creates a match span of 1000+ chars.
   Centering on the midpoint of that span lands in random text. Should center
   on match start so the triggering term is always visible.
2. Excerpt slices at character positions, producing fragments like '...ts, and'.
   Should snap to word boundaries.
"""

import re

from cc_explorer.formatting import _match_example


class TestMatchExampleCentering:
    """Excerpts should start at the match, not center on the midpoint of a greedy span."""

    def test_match_visible_at_start_of_excerpt(self):
        """The matched term should appear near the start of the excerpt, not buried in the middle."""
        text = "x " * 200 + "comment_count is zero here" + " y" * 200
        pattern = re.compile("comment_count", re.IGNORECASE)
        result = _match_example(text, pattern, width=80)
        assert "comment_count" in result

    def test_greedy_regex_still_shows_match_start(self):
        """A greedy .* pattern that spans 500+ chars should still show where the match begins."""
        # Simulate: comment_count ... (500 chars of table) ... 0
        text = "prefix " + "comment_count" + " | " + "x " * 250 + "0" + " | " + "y " * 100
        pattern = re.compile(r"comment_count.*0", re.IGNORECASE)
        result = _match_example(text, pattern, width=80)
        assert "comment_count" in result

    def test_short_match_near_start_of_text(self):
        """Match near the beginning — excerpt shouldn't have a leading '...'."""
        text = "comment_count is the field we care about" + " filler" * 50
        pattern = re.compile("comment_count", re.IGNORECASE)
        result = _match_example(text, pattern, width=60)
        assert result.startswith("comment_count")
        assert "..." not in result[:15]  # no leading ellipsis

    def test_match_at_end_of_text(self):
        """Match near the end — excerpt should show trailing context."""
        text = "filler " * 50 + "comment_count is zero"
        pattern = re.compile("comment_count", re.IGNORECASE)
        result = _match_example(text, pattern, width=60)
        assert "comment_count" in result


class TestMatchExampleWordBoundaries:
    """Excerpts should not slice mid-word."""

    def test_no_leading_word_fragment(self):
        """The excerpt should not start with a partial word."""
        text = "the transactions and the database comment_count field is important for tracking"
        pattern = re.compile("comment_count", re.IGNORECASE)
        # Width chosen so naive slicing would cut "transactions" or "database"
        result = _match_example(text, pattern, width=50)
        assert "comment_count" in result
        # After the leading '...', the first char should be a word start (letter after space or start)
        if result.startswith("..."):
            after_ellipsis = result[3:]
            # Should not start with a lowercase letter fragment (like 'ts ' or 'se ')
            assert after_ellipsis[0] == " " or after_ellipsis.lstrip()[0].isupper() or after_ellipsis.startswith(" ") or not after_ellipsis[0].isalpha() or after_ellipsis[0] == result[3]

    def test_no_trailing_word_fragment(self):
        """The excerpt should not end with a partial word before '...'."""
        text = "comment_count field is important for tracking all the transactions in the database system"
        pattern = re.compile("comment_count", re.IGNORECASE)
        result = _match_example(text, pattern, width=60)
        if result.endswith("..."):
            before_ellipsis = result[:-3]
            # Should end at a word boundary — last char is space or end of a complete word
            assert before_ellipsis[-1] == " " or before_ellipsis.endswith((".", ",", ";", ":", ")", "]", "}")) or before_ellipsis[-1].isalnum()
