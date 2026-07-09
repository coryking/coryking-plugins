"""Tests for cross-project session resolution safety.

When `projects` is omitted, session-keyed tools span the whole corpus. Three
guarantees this exercises, now at the corpus/resolve layer:
- ambiguous short prefixes raise instead of silently resolving to one session
- pooling de-dupes by session_id (so an explicit list naming two worktrees of
  one repo doesn't double-count)
- the holding session is located by filename identity alone (Corpus.discover +
  narrow_to_ids — load_conversations listings, no transcript parse).
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from fastmcp.exceptions import ToolError

import cc_explorer.corpus as corpus_mod
from cc_explorer.corpus import Corpus, SessionRef
from cc_explorer.parser import ConversationRef
from cc_explorer.resolve import resolve_unique_ref, resolve_unique_ref_or_none
from cc_explorer.search import SessionInfo
from cc_explorer.utils import PrefixId

ID_A = "aaaaaaaa-1111-2222-3333-444444444444"
ID_A2 = "aaaaaaaa-9999-8888-7777-666666666666"  # shares 8-char prefix with ID_A
ID_B = "bbbbbbbb-1111-2222-3333-444444444444"


def _ref(sid: str, project: str = "/repo") -> SessionRef:
    return SessionRef(
        session_id=PrefixId(sid),
        path=Path(f"/tmp/{sid}.jsonl"),
        project_path=project,
    )


# --- resolve_unique_ref --------------------------------------------------------


def test_resolve_unique_returns_single_match():
    refs = [_ref(ID_A), _ref(ID_B)]
    got = resolve_unique_ref(refs, ID_A)
    assert got.session_id == PrefixId(ID_A)


def test_resolve_unique_raises_on_ambiguous_prefix():
    # Two distinct full ids sharing the same 8-char prefix, both real sessions
    # (SessionInfo.load succeeds for both) — a genuine collision.
    refs = [_ref(ID_A, "/repoA"), _ref(ID_A2, "/repoB")]
    with patch.object(SessionInfo, "load", classmethod(lambda cls, ref: object())):
        with pytest.raises(ToolError) as exc:
            resolve_unique_ref(refs, "aaaaaaaa")
    msg = str(exc.value)
    assert "ambiguous" in msg.lower()
    assert "/repoA" in msg and "/repoB" in msg  # names where the collision lives


def test_resolve_unique_raises_on_no_match():
    with pytest.raises(ToolError):
        resolve_unique_ref([_ref(ID_B)], ID_A)


def test_resolve_unique_or_none_returns_none_on_no_match():
    assert resolve_unique_ref_or_none([_ref(ID_B)], ID_A) is None


def test_resolve_unique_or_none_still_raises_on_ambiguity():
    refs = [_ref(ID_A, "/repoA"), _ref(ID_A2, "/repoB")]
    with patch.object(SessionInfo, "load", classmethod(lambda cls, ref: object())):
        with pytest.raises(ToolError):
            resolve_unique_ref_or_none(refs, "aaaaaaaa")


# --- ambiguity over empty/unparseable sessions (#9) -----------------------------
#
# Filename-level SessionRefs can't tell an empty/unreadable session file from a
# real one, so a prefix colliding with an empty file looked "ambiguous" until
# resolve_unique_ref_or_none promotes just the colliding refs and drops the
# ones that load empty — restoring the pre-redesign behavior of deciding
# ambiguity over sessions that actually parse non-empty.


def test_ambiguous_prefix_resolves_when_one_collision_is_empty():
    refs = [_ref(ID_A, "/repoA"), _ref(ID_A2, "/repoB")]
    real = object()  # stands in for a promoted SessionInfo — only None-ness matters
    loaded = {ID_A: real, ID_A2: None}

    with patch.object(
        SessionInfo,
        "load",
        classmethod(lambda cls, ref: loaded[ref.session_id.full]),
    ):
        got = resolve_unique_ref_or_none(refs, "aaaaaaaa")
    assert got is not None
    assert got.session_id == PrefixId(ID_A)


def test_ambiguous_prefix_still_raises_when_both_collisions_are_real():
    refs = [_ref(ID_A, "/repoA"), _ref(ID_A2, "/repoB")]

    with patch.object(
        SessionInfo, "load", classmethod(lambda cls, ref: object())
    ):
        with pytest.raises(ToolError) as exc:
            resolve_unique_ref_or_none(refs, "aaaaaaaa")
    msg = str(exc.value)
    assert "ambiguous" in msg.lower()
    assert "/repoA" in msg and "/repoB" in msg


def test_ambiguous_prefix_returns_none_when_both_collisions_are_empty():
    refs = [_ref(ID_A, "/repoA"), _ref(ID_A2, "/repoB")]

    with patch.object(SessionInfo, "load", classmethod(lambda cls, ref: None)):
        assert resolve_unique_ref_or_none(refs, "aaaaaaaa") is None


# --- Corpus.discover dedup -----------------------------------------------------


def test_discover_dedups_by_session_id(monkeypatch):
    # Two project paths (e.g. two worktrees of one repo) each pool the same
    # session — must appear once, not twice.
    monkeypatch.setattr(
        corpus_mod, "resolve_projects", lambda projects=None: ["/repo", "/repo/wt"]
    )
    monkeypatch.setattr(
        corpus_mod,
        "load_conversations",
        lambda p: {PrefixId(ID_A): ConversationRef(path=Path(f"/tmp/{ID_A}.jsonl"), worktree=None)},
    )
    corpus = Corpus.discover(["/repo", "/repo/wt"])
    assert len(corpus.refs) == 1
    assert corpus.refs[0].project_path == "/repo"


# --- narrow_to_ids (cheap locator) ----------------------------------------------


def test_narrow_to_ids_locates_holding_session(monkeypatch):
    monkeypatch.setattr(
        corpus_mod, "resolve_projects", lambda projects=None: ["/a", "/b"]
    )
    convs = {
        "/a": {PrefixId(ID_A): ConversationRef(path=Path(f"/tmp/{ID_A}.jsonl"), worktree=None)},
        "/b": {PrefixId(ID_B): ConversationRef(path=Path(f"/tmp/{ID_B}.jsonl"), worktree=None)},
    }
    monkeypatch.setattr(corpus_mod, "load_conversations", lambda p: convs[p])

    corpus = Corpus.discover(None)
    # Query by 8-char prefix of ID_A → only /a's session matches.
    narrowed = corpus.narrow_to_ids(["aaaaaaaa"])
    assert [r.project_path for r in narrowed.refs] == ["/a"]
    # A prefix nobody has → empty (caller turns this into a "no session" error).
    assert corpus.narrow_to_ids(["cccccccc"]).refs == []
