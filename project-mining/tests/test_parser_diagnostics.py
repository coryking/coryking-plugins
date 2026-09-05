"""Parser noise must not crowd the MCP answer out of a capped capture (#80)."""

import asyncio
import json
import os
from pathlib import Path

import pytest

from cc_explorer.corpus import Corpus, SessionRef
from cc_explorer.mcp_server import mcp
from cc_explorer.parser import collect_parser_diagnostics, load_transcript


def write_transcript(path: Path, broken: bool = True, matching: bool = True) -> None:
    entry = {
        "type": "user", "uuid": "aaaaaaaa-1111-2222-3333-444444444444",
        "timestamp": "2026-03-15T10:30:00Z", "sessionId": path.stem,
        "message": {"role": "user", "content": "DIAGNOSTIC_TARGET" if matching else "valid content"},
    }
    # The raw prefilter selects every file; only one has a conversation hit.
    # This gives a small complete result after a genuinely large parse scan.
    lines = [json.dumps(entry), '{"type":"agent-setting","marker":"DIAGNOSTIC_TARGET"}',
             '{"type":"bridge-session"}']
    if broken:
        lines += ["{invalid json", '{"type":"user","message":{}}']
    path.write_text("\n".join(lines) + "\n")


@pytest.mark.parametrize("file_count", [1, 500])
def test_search_result_survives_capture_cap(tmp_path, monkeypatch, capsys, file_count):
    refs = []
    for index in range(file_count):
        path = tmp_path / f"{index:08x}-1111-2222-3333-444444444444.jsonl"
        write_transcript(path, matching=index == 0)
        refs.append(SessionRef(session_id=path.stem, path=path,
                               project_path=str(tmp_path), worktree=None))
    # Only discovery is substituted: parsing, cache, search, serialization, and
    # the FastMCP middleware/thread boundary are real.
    monkeypatch.setattr(Corpus, "discover", classmethod(lambda cls, *a, **kw: Corpus(refs)))
    for _ in range(2):  # cold and warm cache must report the same facts
        result = asyncio.run(mcp.call_tool("search_projects", {
            "patterns": ["DIAGNOSTIC_TARGET"],
        }))
        captured = capsys.readouterr()
        assert captured.out == ""
        assert len(captured.err.splitlines()) == 1
        assert len(captured.err) < 512
        assert f"{file_count} transcript(s)" in captured.err
        assert f"{file_count * 2} malformed" in captured.err
        assert f"{file_count * 2} unsupported" in captured.err
        serialized = json.dumps(result.structured_content)
        visible = (captured.err + serialized)[:20_000]
        assert serialized in visible
        assert "DIAGNOSTIC_TARGET" in visible
        assert result.structured_content["total_hits"] == 1


def test_unsupported_records_are_not_malformed(tmp_path, capsys):
    path = tmp_path / "unsupported.jsonl"
    write_transcript(path, broken=False)
    assert len(load_transcript(path)) == 1
    err = capsys.readouterr().err
    assert "2 unsupported" in err
    assert "0 malformed" in err
    assert "unparseable" not in err


def test_debug_details_include_cached_file_counts(tmp_path, monkeypatch, capsys):
    path = tmp_path / "debug.jsonl"
    write_transcript(path)
    load_transcript(path)
    capsys.readouterr()
    monkeypatch.setenv("CC_EXPLORER_PARSER_DEBUG", "1")
    load_transcript(path)
    err = capsys.readouterr().err
    assert str(path) in err
    assert "2 malformed" in err and "2 unsupported" in err


@pytest.mark.parametrize("record", ["[]", "null", "42", '{}', '{"type": []}'])
def test_invalid_record_shapes_count_as_malformed(tmp_path, capsys, record):
    path = tmp_path / "invalid.jsonl"
    path.write_text(record + "\n")
    assert load_transcript(path) == []
    assert "1 malformed" in capsys.readouterr().err


def test_batch_deduplicates_even_when_file_is_uncached(tmp_path, monkeypatch, capsys):
    from cc_explorer import parser

    monkeypatch.setattr(parser, "_cache", parser.TranscriptCache(max_bytes=1))
    path = tmp_path / "large.jsonl"
    write_transcript(path)
    with collect_parser_diagnostics():
        load_transcript(path)
        with collect_parser_diagnostics():
            load_transcript(path)
        assert capsys.readouterr().err == ""
    err = capsys.readouterr().err
    assert len(err.splitlines()) == 1
    assert "1 transcript(s): 2 malformed, 2 unsupported" in err


def test_changed_file_replaces_cached_diagnostics(tmp_path, capsys):
    path = tmp_path / "changed.jsonl"
    write_transcript(path)
    load_transcript(path)
    capsys.readouterr()
    previous_mtime = path.stat().st_mtime
    write_transcript(path, broken=False)
    os.utime(path, (previous_mtime + 1, previous_mtime + 1))
    load_transcript(path)
    assert "0 malformed, 2 unsupported" in capsys.readouterr().err


def test_concurrent_calls_do_not_merge_diagnostics(tmp_path, capsys):
    from fastmcp import FastMCP
    from cc_explorer.mcp_server import ParserDiagnosticsMiddleware

    server = FastMCP("diagnostics-test")
    server.add_middleware(ParserDiagnosticsMiddleware())
    paths = [tmp_path / "first.jsonl", tmp_path / "second.jsonl"]
    write_transcript(paths[0])
    write_transcript(paths[1], broken=False)

    async def run():
        both_started = asyncio.Event()
        started = 0

        @server.tool
        async def scan(index: int) -> dict:
            nonlocal started
            await asyncio.to_thread(load_transcript, paths[index])
            started += 1
            if started == 2:
                both_started.set()
            await both_started.wait()
            return {"index": index}

        return await asyncio.wait_for(asyncio.gather(
            server.call_tool("scan", {"index": 0}),
            server.call_tool("scan", {"index": 1}),
        ), timeout=10)

    results = asyncio.run(run())
    assert [r.structured_content for r in results] == [{"index": 0}, {"index": 1}]
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 2
    assert all("1 transcript(s)" in line for line in lines)
    assert any("2 malformed, 2 unsupported" in line for line in lines)
    assert any("0 malformed, 2 unsupported" in line for line in lines)


def test_failed_call_reports_and_does_not_leak_into_next_call(tmp_path, capsys):
    from fastmcp import FastMCP
    from fastmcp.exceptions import ToolError
    from cc_explorer.mcp_server import ParserDiagnosticsMiddleware

    server = FastMCP("diagnostics-test")
    server.add_middleware(ParserDiagnosticsMiddleware())
    path = tmp_path / "failure.jsonl"
    write_transcript(path)

    @server.tool
    def fail() -> dict:
        load_transcript(path)
        raise ToolError("expected failure")

    @server.tool
    def clean() -> dict:
        return {"ok": True}

    async def run():
        with pytest.raises(ToolError, match="expected failure"):
            await server.call_tool("fail", {})
        err = capsys.readouterr().err
        assert err.count("[cc-explorer parser]") == 1
        assert "2 malformed, 2 unsupported" in err
        result = await server.call_tool("clean", {})
        assert result.structured_content == {"ok": True}
        assert capsys.readouterr().err == ""

    asyncio.run(run())
