"""Tests for mtime-based transcript caching in load_transcript.

The MCP server is a persistent process — caching parsed transcripts
between tool calls avoids re-parsing 323MB of JSONL on every search.
"""

import json
import os
import time
from pathlib import Path

from cc_explorer.parser import load_transcript


def _make_entry(text: str, uuid: str = "aaaaaaaa-1111-2222-3333-444444444444") -> dict:
    """Minimal valid JSONL entry for a human message."""
    return {
        "type": "user",
        "uuid": uuid,
        "timestamp": "2026-03-15T10:30:00Z",
        "sessionId": "bbbbbbbb-1111-2222-3333-444444444444",
        "message": {"role": "user", "content": text},
    }


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


class TestTranscriptCache:
    def test_cache_returns_same_entries_on_second_call(self, tmp_path):
        """Second call with unchanged file returns the same list object."""
        p = tmp_path / "session.jsonl"
        _write_jsonl(p, [_make_entry("hello")])

        first = load_transcript(p)
        second = load_transcript(p)

        assert first is second

    def test_cache_invalidates_on_mtime_change(self, tmp_path):
        """Modifying the file (changing mtime) forces a re-parse."""
        p = tmp_path / "session.jsonl"
        _write_jsonl(p, [_make_entry("hello")])

        first = load_transcript(p)
        assert len(first) == 1

        # Ensure mtime actually changes (some filesystems have 1s granularity)
        time.sleep(0.05)
        _write_jsonl(p, [_make_entry("hello"), _make_entry("world", uuid="cccccccc-1111-2222-3333-444444444444")])
        # Force mtime change on filesystems with coarse granularity
        os.utime(p, (time.time() + 1, time.time() + 1))

        second = load_transcript(p)

        assert second is not first
        assert len(second) == 2

    def test_cache_different_paths_independent(self, tmp_path):
        """Each file path has its own cache entry."""
        p1 = tmp_path / "session1.jsonl"
        p2 = tmp_path / "session2.jsonl"
        _write_jsonl(p1, [_make_entry("alpha")])
        _write_jsonl(p2, [_make_entry("beta")])

        entries1 = load_transcript(p1)
        entries2 = load_transcript(p2)

        assert entries1 is not entries2

        # Modify p1, p2 should still return cached
        time.sleep(0.05)
        _write_jsonl(p1, [_make_entry("alpha modified")])
        os.utime(p1, (time.time() + 1, time.time() + 1))

        entries1_new = load_transcript(p1)
        entries2_same = load_transcript(p2)

        assert entries1_new is not entries1  # p1 invalidated
        assert entries2_same is entries2  # p2 still cached
