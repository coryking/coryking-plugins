"""Tests for format_tool_input — per-tool-name field selection and truncation."""

import pytest

from cc_explorer.models import format_tool_input


class TestFormatToolInput:
    def test_zero_returns_full_json(self):
        result = format_tool_input("Read", {"file_path": "/tmp/foo.py"}, truncate=0)
        assert '"file_path": "/tmp/foo.py"' in result

    @pytest.mark.parametrize(
        "name,inputs,expected",
        [
            ("Read", {"file_path": "/tmp/foo.py"}, "/tmp/foo.py"),
            ("Bash", {"command": "git status"}, "git status"),
            ("Grep", {"pattern": "foo", "path": "/src"}, "/foo/ /src"),
            ("Edit", {"file_path": "/tmp/bar.py"}, "/tmp/bar.py"),
            ("Write", {"file_path": "/tmp/baz.py"}, "/tmp/baz.py"),
            ("Glob", {"pattern": "**/*.py", "path": "/src"}, "**/*.py in /src"),
            ("WebFetch", {"url": "https://example.com"}, "https://example.com"),
            ("navigate", {"url": "https://example.com"}, "https://example.com"),
            ("javascript_tool", {"text": "document.title"}, "document.title"),
            ("SomeTool", {"x": 1}, "{'x': 1}"),
        ],
    )
    def test_selects_relevant_input(self, name, inputs, expected):
        assert format_tool_input(name, inputs, truncate=80) == expected

    def test_truncation_applied_to_long_input(self):
        long_cmd = "git log --oneline " + "a" * 200
        result = format_tool_input("Bash", {"command": long_cmd}, truncate=40)
        assert len(result) <= 40
        assert result.endswith("...")

    def test_truncation_prefers_word_boundary(self):
        cmd = "git status && git diff --staged && git log --oneline"
        result = format_tool_input("Bash", {"command": cmd}, truncate=30)
        assert result == "git status && git diff..."
