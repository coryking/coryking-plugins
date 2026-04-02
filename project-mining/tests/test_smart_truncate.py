"""Tests for smart_truncate — word-boundary-preferred truncation."""

from cc_explorer.utils import smart_truncate


class TestSmartTruncate:
    def test_zero_means_no_truncation(self):
        text = "a" * 10000
        assert smart_truncate(text, 0) == text

    def test_short_text_unchanged(self):
        assert smart_truncate("hello world", 100) == "hello world"

    def test_exact_length_unchanged(self):
        assert smart_truncate("hello", 5) == "hello"

    def test_breaks_at_word_boundary(self):
        result = smart_truncate("hello world goodbye", 16)
        assert result == "hello world..."
        assert len(result) <= 16

    def test_hard_cut_when_no_spaces(self):
        """File paths, URLs, identifiers — no word boundaries."""
        result = smart_truncate("/very/long/path/to/some/deeply/nested/file.py", 20)
        assert result.endswith("...")
        assert len(result) <= 20

    def test_hard_cut_single_long_word(self):
        result = smart_truncate("abcdefghijklmnopqrstuvwxyz", 10)
        assert result == "abcdefg..."
        assert len(result) == 10

    def test_empty_string(self):
        assert smart_truncate("", 10) == ""

    def test_custom_placeholder(self):
        result = smart_truncate("hello world goodbye", 16, placeholder="~")
        assert result.endswith("~")

    def test_whitespace_collapsed(self):
        """textwrap.shorten collapses whitespace — verify we handle it."""
        result = smart_truncate("hello    world", 20)
        # Should still work, whitespace collapsed is fine for display
        assert "hello" in result
