"""Tests for the corpus identity layer and the raw-byte prefilter.

The load-bearing invariant: `Corpus.candidate_refs` returns a SUPERSET of the
sessions the typed matcher would find hits in — the prefilter may over-select
(raw bytes contain system XML the matcher strips) but must NEVER drop a true
hit. Patterns that could false-negative against JSON string escaping are gated
by `rg_safe` to the scan-all path; this file pins both the gate and the
end-to-end superset property, including the stripped-XML/newline boundary case.
"""

import json
import shutil
from pathlib import Path

import pytest

import cc_explorer.corpus as corpus_mod
from cc_explorer.corpus import (
    Corpus,
    PyScanner,
    RgScanner,
    ScannerError,
    SessionRef,
    rg_safe,
)
from cc_explorer.search import SessionInfo, promote_refs, triage
from cc_explorer.utils import PrefixId

SID_A = "aaaaaaaa-1111-2222-3333-444444444444"
SID_B = "bbbbbbbb-1111-2222-3333-444444444444"
AGENT_ID = "a" + "f" * 16


def _entry(text, uuid="11111111-aaaa-bbbb-cccc-dddddddddddd", session=SID_A, blocks=None):
    """A human JSONL entry; `blocks` overrides content with multiple text blocks."""
    content = blocks if blocks is not None else [{"type": "text", "text": text}]
    return {
        "type": "user",
        "uuid": uuid,
        "timestamp": "2026-06-15T10:30:00Z",
        "sessionId": session,
        "message": {"role": "user", "content": content},
    }


def _write_session(
    enc_dir: Path,
    sid: str,
    entries: list[dict],
    *,
    ensure_ascii: bool = True,
) -> SessionRef:
    enc_dir.mkdir(parents=True, exist_ok=True)
    path = enc_dir / f"{sid}.jsonl"
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=ensure_ascii) + "\n")
    return SessionRef(session_id=PrefixId(sid), path=path, project_path="/fake")


def _write_agent(ref: SessionRef, agent_id: str, entries: list[dict]) -> Path:
    subdir = ref.path.with_suffix("") / "subagents"
    subdir.mkdir(parents=True, exist_ok=True)
    path = subdir / f"agent-{agent_id}.jsonl"
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    return path


# =============================================================================
# rg_safe — the false-negative gate
# =============================================================================


@pytest.mark.parametrize(
    "pattern",
    [
        "hello",
        "foo.*bar",
        "colou?r",
        r"woggle\d+",
        r"foo\.bar",
        "alpha|beta",
        r"\bword\b",
        "case-neutral",
        "a.*b",  # `.*` absorbs a multi-char escape
        "a.+b",  # `.+` absorbs a multi-char escape
        r"a\.b",  # escaped literal dot — never rewritten by JSON escaping
        r"[0-9]+",  # numeric ranges do not participate in case folding
        r"[a-h]+",  # ASCII letter range that excludes I/i
        r"[J-Z]+",  # ASCII letter range that excludes I/i
    ],
)
def test_rg_safe_accepts_plain_patterns(pattern):
    assert rg_safe(pattern)


@pytest.mark.parametrize(
    "pattern",
    [
        'say "hello"',      # literal quote — \" in raw JSONL
        r"foo\s+bar",       # \s may need to match a real newline
        r"a\nb",            # escaped newline literal
        r"a\tb",            # escaped tab literal
        r"[^x]y",           # negated class matches newline
        "^start",           # line anchors mean raw JSONL lines, not turns
        "end$",
        "(?i)flags",        # groups with flags / lookaround
        r"back\1ref",       # backreference (rust-regex rejects anyway)
        r"foo\\bar",        # literal backslash — doubled in raw
        r"\Sx",             # \S over-approximates around escapes
        r"\Wfoo",           # \W can be required to match a newline
        r"\Dbar",
        r"\Astart",
        "a.b",              # bare `.` matches one char, raw may hold a 2-char escape
        "a.?b",             # `.?` still hits the single-char hazard
    ],
)
def test_rg_safe_rejects_escaping_hazards(pattern):
    assert not rg_safe(pattern)


@pytest.mark.parametrize(
    "pattern",
    [
        "din",  # literal i also matches Python's extra İ/ı pair
        "DIN",
        "d[i]n",  # literal i inside a positive character class
        "d[a-z]n",  # range includes i
        "d[H-J]n",  # uppercase range includes I
        "d[A-h]n",  # mixed-case ASCII range includes uppercase I
        "d[J-z]n",  # mixed-case ASCII range includes lowercase i
        "dİn",  # non-ASCII pattern: engine Unicode versions may differ
    ],
)
def test_rg_safe_rejects_unproven_unicode_case_folding(pattern):
    assert not rg_safe(pattern)


# =============================================================================
# Scanners — same contract for rg and the Python fallback
# =============================================================================


def _scanners():
    out = [PyScanner()]
    rg = shutil.which("rg")
    if rg:
        out.append(RgScanner(rg))
    return out


@pytest.mark.parametrize("scanner", _scanners(), ids=lambda s: type(s).__name__)
def test_scanner_finds_matching_files_only(tmp_path, scanner):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    a.write_text(json.dumps(_entry("the sandwich filter is ready")) + "\n")
    b.write_text(json.dumps(_entry("pruning strategy looks good")) + "\n")

    hits = scanner.files_with_match(["sandwich"], [a, b])
    assert hits == {a}


@pytest.mark.parametrize("scanner", _scanners(), ids=lambda s: type(s).__name__)
def test_scanner_multiple_patterns_union(tmp_path, scanner):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    c = tmp_path / "c.jsonl"
    a.write_text(json.dumps(_entry("alpha content")) + "\n")
    b.write_text(json.dumps(_entry("beta content")) + "\n")
    c.write_text(json.dumps(_entry("gamma content")) + "\n")

    hits = scanner.files_with_match(["alpha", "beta"], [a, b, c])
    assert hits == {a, b}


@pytest.mark.parametrize("scanner", _scanners(), ids=lambda s: type(s).__name__)
def test_scanner_case_insensitive(tmp_path, scanner):
    a = tmp_path / "a.jsonl"
    a.write_text(json.dumps(_entry("The SANDWICH Filter")) + "\n")
    assert scanner.files_with_match(["sandwich"], [a]) == {a}


def test_rg_scanner_batches_argv(tmp_path, monkeypatch):
    """Many files still all get scanned when the argv budget forces batching."""
    rg = shutil.which("rg")
    if not rg:
        pytest.skip("rg not on PATH")
    files = []
    for i in range(20):
        p = tmp_path / f"s{i}.jsonl"
        p.write_text(json.dumps(_entry(f"needle-{i} here")) + "\n")
        files.append(p)
    # Force a batch flush every file or two.
    monkeypatch.setattr(corpus_mod, "_ARGV_BUDGET", 10)

    hits = RgScanner(rg).files_with_match(["needle-"], files)
    assert hits == set(files)


def test_py_scanner_skips_vanished_file(tmp_path):
    """A file that disappears between listing and opening is skipped, not
    fatal — it can't be parsed later either, so treating it as no-match is
    consistent (unlike an unreadable-but-present file, which must raise)."""
    a = tmp_path / "a.jsonl"
    a.write_text(json.dumps(_entry("alpha content")) + "\n")
    missing = tmp_path / "gone.jsonl"

    hits = PyScanner().files_with_match(["alpha"], [a, missing])
    assert hits == {a}


def test_py_scanner_raises_scanner_error_on_permission_denied(tmp_path, monkeypatch):
    """An unreadable-but-present file must NOT silently read as no-match — that
    would under-select against the candidate-superset contract. It raises
    ScannerError so candidate_refs falls back to scan-all instead."""
    a = tmp_path / "a.jsonl"
    a.write_text(json.dumps(_entry("alpha content")) + "\n")

    real_open = open

    def boom_open(path, *a_, **kw):
        if str(path) == str(a):
            raise PermissionError("simulated permission denied")
        return real_open(path, *a_, **kw)

    monkeypatch.setattr("builtins.open", boom_open)

    with pytest.raises(ScannerError):
        PyScanner().files_with_match(["alpha"], [a])


def test_rg_scanner_raises_on_bad_pattern(tmp_path):
    """A pattern the rust engine rejects surfaces as ScannerError (callers
    fall back to scan-all), never a silent empty candidate set."""
    rg = shutil.which("rg")
    if not rg:
        pytest.skip("rg not on PATH")
    a = tmp_path / "a.jsonl"
    a.write_text("content\n")
    with pytest.raises(ScannerError):
        RgScanner(rg).files_with_match(["foo(?=bar)"], [a])


# =============================================================================
# Corpus.candidate_refs — the superset invariant
# =============================================================================


def test_candidate_refs_selects_matching_session(tmp_path):
    enc = tmp_path / "enc"
    ref_a = _write_session(enc, SID_A, [_entry("we discussed the hamburger filter")])
    ref_b = _write_session(enc, SID_B, [_entry("pruning strategy", session=SID_B)])
    corpus = Corpus([ref_a, ref_b])

    candidates = corpus.candidate_refs(["hamburger"])
    assert candidates == [ref_a]


def test_candidate_refs_finds_hit_inside_subagent_body(tmp_path):
    """A pattern that lives only in a subagent transcript still selects the
    parent session ref — the search corpus spans subagent bodies (#22)."""
    enc = tmp_path / "enc"
    ref_a = _write_session(enc, SID_A, [_entry("parent says nothing special")])
    _write_agent(ref_a, AGENT_ID, [_entry("the xylophone secret lives here")])
    ref_b = _write_session(enc, SID_B, [_entry("unrelated", session=SID_B)])
    corpus = Corpus([ref_a, ref_b])

    assert corpus.candidate_refs(["xylophone"]) == [ref_a]


def test_unsafe_pattern_forces_scan_all(tmp_path):
    enc = tmp_path / "enc"
    ref_a = _write_session(enc, SID_A, [_entry("alpha")])
    ref_b = _write_session(enc, SID_B, [_entry("beta", session=SID_B)])
    corpus = Corpus([ref_a, ref_b])

    # \s+ is an escaping hazard → every ref is a candidate.
    assert corpus.candidate_refs([r"alpha\s+beta"]) == [ref_a, ref_b]


def test_scanner_failure_forces_scan_all(tmp_path, monkeypatch, capsys):
    """A scanner that raises at runtime (not just an unsafe pattern) must also
    fall back to scan-all — under-selecting here would silently drop a true
    hit, not just miss an optimization. The failure is also noted on stderr
    (stdout is the stdio MCP channel) so a real prefilter breakage is visible
    rather than indistinguishable from an empty-corpus no-op."""
    enc = tmp_path / "enc"
    ref_a = _write_session(enc, SID_A, [_entry("alpha")])
    ref_b = _write_session(enc, SID_B, [_entry("beta", session=SID_B)])
    corpus = Corpus([ref_a, ref_b])

    class BoomScanner:
        def files_with_match(self, patterns, files):
            raise ScannerError("simulated scanner crash")

    monkeypatch.setattr(corpus_mod, "make_scanner", lambda: BoomScanner())

    assert corpus.candidate_refs(["alpha"]) == [ref_a, ref_b]
    err = capsys.readouterr().err
    assert "prefilter failed" in err
    assert "simulated scanner crash" in err


def test_mixed_safe_and_unsafe_patterns_scan_all(tmp_path):
    """One unsafe pattern in the set disables the prefilter for the call —
    candidates must be a superset for EVERY pattern simultaneously."""
    enc = tmp_path / "enc"
    ref_a = _write_session(enc, SID_A, [_entry("alpha")])
    ref_b = _write_session(enc, SID_B, [_entry("beta", session=SID_B)])
    corpus = Corpus([ref_a, ref_b])

    assert corpus.candidate_refs(["alpha", r"x\s+y"]) == [ref_a, ref_b]


def test_superset_at_stripped_xml_newline_boundary(tmp_path):
    """The case the rg_safe gate exists for: extracted text joins content
    blocks with a REAL newline, so `alpha\\s+beta` matches in the typed
    matcher — but raw JSONL has the blocks as separate JSON strings where \\s
    can never match. The pattern must route to scan-all so the prefilter
    cannot drop the true hit."""
    enc = tmp_path / "enc"
    ref = _write_session(
        enc,
        SID_A,
        [
            _entry(
                "",
                blocks=[
                    {"type": "text", "text": "this ends with alpha"},
                    {"type": "text", "text": "beta starts this one"},
                ],
            )
        ],
    )
    corpus = Corpus([ref])
    pattern = r"alpha\s+beta"

    # The typed matcher finds the hit (extract_text joins blocks with \n)...
    info = SessionInfo.load(ref)
    assert info is not None
    results = triage([info], pattern)
    assert results and results[0].count == 1

    # ...and the prefilter keeps the session as a candidate (scan-all route).
    assert not rg_safe(pattern)
    assert corpus.candidate_refs([pattern]) == [ref]


def test_superset_at_bare_dot_escape_boundary(tmp_path):
    """A second case the rg_safe gate exists for: a bare `.` matches exactly
    one char, so `a.b` matches extracted `a"b` but NOT raw JSONL bytes, where
    the quote is escaped to the two-char `a\\"b`. The pattern must route to
    scan-all so the prefilter cannot drop the true hit."""
    enc = tmp_path / "enc"
    ref = _write_session(enc, SID_A, [_entry('a"b in the middle')])
    corpus = Corpus([ref])

    assert not rg_safe("a.b")
    assert corpus.candidate_refs(["a.b"]) == [ref]


def test_unicode_casefold_literal_json_uses_safe_fallback(tmp_path):
    """Literal UTF-8 makes the engine mismatch visible: the decoded matcher
    accepts dİn for `din`, so an rg prefilter must not be allowed to drop it."""
    enc = tmp_path / "enc"
    matching = _write_session(
        enc,
        SID_A,
        [_entry("word dİn here")],
        ensure_ascii=False,
    )
    unrelated = _write_session(enc, SID_B, [_entry("unrelated", session=SID_B)])
    corpus = Corpus([matching, unrelated])

    raw = matching.path.read_text()
    assert "dİn" in raw
    assert r"d\u0130n" not in raw
    oracle = triage(promote_refs(corpus.refs), "din")
    assert [result.session.session_id for result in oracle] == [matching.session_id]

    # The Python fallback sees the literal character with the authoritative
    # folding semantics without promoting the unrelated session.
    assert corpus.candidate_refs(["din"]) == [matching]


def test_unicode_casefold_escaped_json_uses_safe_fallback(tmp_path):
    """ASCII-escaped JSON is a separate hazard: neither raw scanner can see
    decoded dİn as `din` without the supplemental raw-escape candidate."""
    enc = tmp_path / "enc"
    matching = _write_session(enc, SID_A, [_entry("word dİn here")])
    unrelated = _write_session(enc, SID_B, [_entry("unrelated", session=SID_B)])
    corpus = Corpus([matching, unrelated])

    raw = matching.path.read_text()
    assert "dİn" not in raw
    assert r"d\u0130n" in raw
    oracle = triage(promote_refs(corpus.refs), "din")
    assert [result.session.session_id for result in oracle] == [matching.session_id]

    # The supplemental raw-escape pattern conservatively selects this file;
    # the typed matcher above remains the oracle for whether it is a real hit.
    assert corpus.candidate_refs(["din"]) == [matching]


def test_prefilter_equivalent_to_full_scan_for_safe_patterns(tmp_path):
    """End-to-end superset check: triage over candidate refs produces exactly
    the same results as triage over every ref, for prefilter-safe patterns."""
    enc = tmp_path / "enc"
    ref_a = _write_session(
        enc, SID_A, [_entry("we need to capture everything in the database")]
    )
    ref_b = _write_session(
        enc,
        SID_B,
        [
            _entry("the double pruning strategy looks good", session=SID_B),
            _entry(
                "let's check the export path",
                uuid="44444444-aaaa-bbbb-cccc-dddddddddddd",
                session=SID_B,
            ),
        ],
    )
    corpus = Corpus([ref_a, ref_b])
    patterns = ["database", "prun", "export", "zzznotfound"]

    all_sessions = [SessionInfo.load(r) for r in corpus.refs]
    all_sessions = [s for s in all_sessions if s]

    for pat in patterns:
        oracle = triage(all_sessions, pat)
        cand_sessions = [
            s
            for r in corpus.candidate_refs([pat])
            if (s := SessionInfo.load(r)) is not None
        ]
        got = triage(cand_sessions, pat)
        assert [(r.session.session_id, r.count) for r in got] == [
            (r.session.session_id, r.count) for r in oracle
        ], f"prefilter changed results for {pat!r}"


# =============================================================================
# narrow_to_artifact_ids
# =============================================================================


def test_narrow_finds_session_by_id(tmp_path):
    enc = tmp_path / "enc"
    ref_a = _write_session(enc, SID_A, [_entry("x")])
    ref_b = _write_session(enc, SID_B, [_entry("y", session=SID_B)])
    corpus = Corpus([ref_a, ref_b])

    narrowed = corpus.narrow_to_artifact_ids([SID_A[:8]])
    assert narrowed.refs == [ref_a]


def test_narrow_finds_holding_session_for_agent_id(tmp_path):
    enc = tmp_path / "enc"
    ref_a = _write_session(enc, SID_A, [_entry("x")])
    _write_agent(ref_a, AGENT_ID, [_entry("agent body")])
    ref_b = _write_session(enc, SID_B, [_entry("y", session=SID_B)])
    corpus = Corpus([ref_a, ref_b])

    narrowed = corpus.narrow_to_artifact_ids([AGENT_ID[:8]])
    assert narrowed.refs == [ref_a]


def test_narrow_finds_workflow_nested_agent_file(tmp_path):
    enc = tmp_path / "enc"
    ref = _write_session(enc, SID_A, [_entry("x")])
    wf = ref.path.with_suffix("") / "subagents" / "workflows" / "wf_run1"
    wf.mkdir(parents=True)
    (wf / f"agent-{AGENT_ID}.jsonl").write_text(json.dumps(_entry("wf body")) + "\n")
    corpus = Corpus([ref])

    assert corpus.narrow_to_artifact_ids([AGENT_ID[:10]]).refs == [ref]


def test_narrow_skips_too_short_ids(tmp_path):
    enc = tmp_path / "enc"
    ref = _write_session(enc, SID_A, [_entry("x")])
    _write_agent(ref, AGENT_ID, [_entry("agent body")])
    corpus = Corpus([ref])

    # <6 chars: not globbed (the resolver raises its own too-short error).
    assert corpus.narrow_to_artifact_ids([AGENT_ID[:4]]).refs == []


# =============================================================================
# SessionInfo.load / promote_refs — a ref whose file vanished
# =============================================================================


def test_session_info_load_returns_none_for_missing_file(tmp_path):
    """A ref pointing at a path that no longer exists (deleted between
    discovery and promotion) is treated like any other unreadable session —
    load returns None rather than raising, per its documented contract."""
    ref = SessionRef(
        session_id=PrefixId(SID_A),
        path=tmp_path / "does-not-exist.jsonl",
        project_path="/fake",
    )
    assert SessionInfo.load(ref) is None


def test_promote_refs_drops_missing_file_ref(tmp_path):
    """One good ref plus one whose file vanished promotes to just the good
    session — promote_refs' whole job is silently dropping load's None."""
    enc = tmp_path / "enc"
    good = _write_session(enc, SID_A, [_entry("real content")])
    missing = SessionRef(
        session_id=PrefixId(SID_B),
        path=tmp_path / "gone.jsonl",
        project_path="/fake",
    )

    sessions = promote_refs([good, missing])
    assert [s.session_id for s in sessions] == [good.session_id]
