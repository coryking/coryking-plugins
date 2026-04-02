"""Tests for format_tool_input — per-tool-name field selection and truncation."""

from cc_explorer.models import format_tool_input


class TestFormatToolInput:
    def test_zero_returns_full_json(self):
        result = format_tool_input("Read", {"file_path": "/tmp/foo.py"}, truncate=0)
        assert '"file_path": "/tmp/foo.py"' in result

    def test_read_extracts_file_path(self):
        result = format_tool_input("Read", {"file_path": "/tmp/foo.py"}, truncate=80)
        assert result == "/tmp/foo.py"

    def test_bash_extracts_command(self):
        result = format_tool_input("Bash", {"command": "git status"}, truncate=80)
        assert result == "git status"

    def test_grep_extracts_pattern_and_path(self):
        result = format_tool_input("Grep", {"pattern": "foo", "path": "/src"}, truncate=80)
        assert result == "/foo/ /src"

    def test_edit_extracts_file_path(self):
        result = format_tool_input("Edit", {"file_path": "/tmp/bar.py"}, truncate=80)
        assert result == "/tmp/bar.py"

    def test_write_extracts_file_path(self):
        result = format_tool_input("Write", {"file_path": "/tmp/baz.py"}, truncate=80)
        assert result == "/tmp/baz.py"

    def test_glob_extracts_pattern(self):
        result = format_tool_input("Glob", {"pattern": "**/*.py", "path": "/src"}, truncate=80)
        assert result == "**/*.py in /src"

    def test_webfetch_extracts_url(self):
        result = format_tool_input("WebFetch", {"url": "https://example.com"}, truncate=80)
        assert result == "https://example.com"

    def test_unknown_tool_stringifies(self):
        result = format_tool_input("SomeTool", {"x": 1}, truncate=80)
        assert "x" in result

    def test_truncation_applied_to_long_input(self):
        long_cmd = "git log --oneline " + "a" * 200
        result = format_tool_input("Bash", {"command": long_cmd}, truncate=40)
        assert len(result) <= 40
        assert result.endswith("...")

    def test_truncation_prefers_word_boundary(self):
        cmd = "git status && git diff --staged && git log --oneline"
        result = format_tool_input("Bash", {"command": cmd}, truncate=30)
        assert result.endswith("...")
        # Should not chop mid-word
        assert "stag..." not in result
