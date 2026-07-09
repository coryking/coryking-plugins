"""Tests for get_agent_detail's no-session resolution path.

get_agent_detail had zero coverage before this file. These tests exercise the
`session` param omitted branch: the too-short guard (MIN_ID_LEN, checked before
any corpus lookup) and the "matches nothing" resolution failure (Corpus.discover
+ narrow_to_artifact_ids + promote_refs coming back empty).
"""

import pytest
from fastmcp.exceptions import ToolError

import cc_explorer.mcp_server as srv
from cc_explorer.corpus import Corpus


def test_short_agent_id_raises_before_any_lookup():
    """An id under MIN_ID_LEN (6 chars) is rejected outright — no corpus
    discovery happens, so this needs no filesystem/monkeypatch setup at all."""
    with pytest.raises(ToolError) as exc:
        srv.get_agent_detail(agent_ids=["abcde"])
    assert "too short" in str(exc.value).lower()


def test_agent_id_matching_nothing_raises_not_found(monkeypatch):
    """A well-formed id that resolves to no holding session raises ToolError
    naming it — Corpus.discover is stubbed empty so this proves the resolution
    seam (narrow_to_artifact_ids + promote_refs) reports failure rather than
    returning a degenerate empty response."""
    monkeypatch.setattr(Corpus, "discover", classmethod(lambda cls, projects=None: Corpus([])))

    with pytest.raises(ToolError) as exc:
        srv.get_agent_detail(agent_ids=["zzzzzzzzzz"])
    assert "not found" in str(exc.value).lower()
