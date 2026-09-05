"""Tests for mtime-based transcript caching in load_transcript.

The MCP server is a persistent process — caching parsed transcripts
between tool calls avoids re-parsing 323MB of JSONL on every search.
"""

import json
import os
from pathlib import Path

import pytest

import cc_explorer.parser as parser_mod
from cc_explorer.parser import (
    _HEAP_FACTOR,
    TranscriptCache,
    _cache_max_bytes,
    load_transcript,
)


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

        previous_mtime = p.stat().st_mtime
        _write_jsonl(p, [_make_entry("hello"), _make_entry("world", uuid="cccccccc-1111-2222-3333-444444444444")])
        # Force mtime change on filesystems with coarse granularity
        os.utime(p, (previous_mtime + 2, previous_mtime + 2))
        assert p.stat().st_mtime != previous_mtime

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
        previous_mtime = p1.stat().st_mtime
        _write_jsonl(p1, [_make_entry("alpha modified")])
        os.utime(p1, (previous_mtime + 2, previous_mtime + 2))
        assert p1.stat().st_mtime != previous_mtime

        entries1_new = load_transcript(p1)
        entries2_same = load_transcript(p2)

        assert entries1_new is not entries1  # p1 invalidated
        assert entries2_same is entries2  # p2 still cached


class TestByteCappedCache:
    """The cache is a byte-capped LRU — no operation may retain the corpus."""

    @pytest.fixture
    def small_cache(self, monkeypatch):
        """Install a tiny cache so a couple of files exceed the cap.

        Each test file below is ~150 bytes raw (~270 charged at x1.8), so a
        400-byte cap holds one file but not two.
        """
        cache = TranscriptCache(max_bytes=400)
        monkeypatch.setattr(parser_mod, "_cache", cache)
        return cache

    def test_eviction_when_over_cap(self, tmp_path, small_cache):
        """Loading past the cap evicts the least-recently-used transcript."""
        p1 = tmp_path / "s1.jsonl"
        p2 = tmp_path / "s2.jsonl"
        _write_jsonl(p1, [_make_entry("alpha")])
        _write_jsonl(p2, [_make_entry("beta")])

        first = load_transcript(p1)
        load_transcript(p2)  # charges past 400 bytes -> evicts p1

        assert small_cache.get(p1.resolve()) is None
        # p1 re-parses into a fresh object rather than erroring
        assert load_transcript(p1) is not first

    def test_recently_used_survives(self, tmp_path, small_cache):
        """The most recently loaded transcript is the one that stays cached."""
        p1 = tmp_path / "s1.jsonl"
        p2 = tmp_path / "s2.jsonl"
        _write_jsonl(p1, [_make_entry("alpha")])
        _write_jsonl(p2, [_make_entry("beta")])

        load_transcript(p1)
        second = load_transcript(p2)

        assert load_transcript(p2) is second

    def test_cap_respected_across_many_files(self, tmp_path, small_cache):
        """Total charged bytes never exceed the cap, however many files load."""
        for i in range(10):
            p = tmp_path / f"s{i}.jsonl"
            _write_jsonl(p, [_make_entry(f"entry number {i}")])
            load_transcript(p)
            assert small_cache._lru.currsize <= small_cache._lru.maxsize

    def test_oversized_file_served_uncached(self, tmp_path, small_cache):
        """A single file bigger than the whole cap parses fine, just uncached."""
        p = tmp_path / "big.jsonl"
        _write_jsonl(p, [_make_entry("x" * 1000)])  # ~1KB raw >> 400-byte cap

        first = load_transcript(p)
        second = load_transcript(p)

        assert len(first) == 1
        assert first is not second  # never cached
        assert small_cache.get(p.resolve()) is None

    def test_nbytes_charged_at_heap_factor(self, tmp_path, small_cache):
        """Entries are charged raw file size x the measured heap factor."""
        p = tmp_path / "s.jsonl"
        _write_jsonl(p, [_make_entry("hello")])
        load_transcript(p)

        cached = small_cache.get(p.resolve())
        assert cached is not None
        assert cached.nbytes == int(p.stat().st_size * _HEAP_FACTOR)


class TestCacheMaxBytes:
    """CC_EXPLORER_CACHE_MB env knob: positive honored, junk falls back."""

    DEFAULT = 200 * 1024 * 1024

    def test_default(self, monkeypatch):
        monkeypatch.delenv("CC_EXPLORER_CACHE_MB", raising=False)
        assert _cache_max_bytes() == self.DEFAULT

    def test_positive_override(self, monkeypatch):
        monkeypatch.setenv("CC_EXPLORER_CACHE_MB", "50")
        assert _cache_max_bytes() == 50 * 1024 * 1024

    @pytest.mark.parametrize("junk", ["0", "-5", "banana", ""])
    def test_junk_falls_back(self, monkeypatch, junk):
        monkeypatch.setenv("CC_EXPLORER_CACHE_MB", junk)
        assert _cache_max_bytes() == self.DEFAULT


class TestSkippedLineReporting:
    """Malformed lines are counted to stderr; structural headers stay silent."""

    def test_malformed_lines_reported(self, tmp_path, capsys):
        p = tmp_path / "bad.jsonl"
        with open(p, "w") as f:
            f.write(json.dumps(_make_entry("good")) + "\n")
            f.write("{not json at all\n")
            f.write(json.dumps({"type": "no-such-entry-type"}) + "\n")

        entries = load_transcript(p)

        assert len(entries) == 1
        err = capsys.readouterr().err
        assert "skipped 2 unparseable line(s)" in err

    def test_structural_headers_silent(self, tmp_path, capsys):
        """mode/permission-mode/custom-title/provenance lines are expected —
        skipped without noise and without inflating the skip count."""
        p = tmp_path / "headers.jsonl"
        with open(p, "w") as f:
            f.write(json.dumps({"type": "mode", "mode": "normal"}) + "\n")
            f.write(json.dumps({"type": "permission-mode", "permissionMode": "default"}) + "\n")
            f.write(json.dumps({"type": "custom-title", "customTitle": "t"}) + "\n")
            f.write(json.dumps({"type": "x-converter-provenance"}) + "\n")
            f.write(json.dumps(_make_entry("real content")) + "\n")

        entries = load_transcript(p)

        assert len(entries) == 1
        assert "skipped" not in capsys.readouterr().err
