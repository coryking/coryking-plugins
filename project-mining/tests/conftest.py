"""Shared test fixtures."""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from cc_explorer.models import HumanEntry, TextContent, TranscriptStats, UserMessageModel
from cc_explorer.search import SessionInfo

FULL_UUID = "a9529cc1-b576-5fd3-9f1a-1234567890ab"
TS = datetime(2026, 3, 15, 10, 30, 0, tzinfo=timezone.utc)


@pytest.fixture
def full_uuid():
    return FULL_UUID


@pytest.fixture
def human_entry():
    return HumanEntry(
        uuid=FULL_UUID,
        timestamp=TS,
        sessionId="bbbbbbbb-1111-2222-3333-444444444444",
        type="user",
        message=UserMessageModel(
            role="user",
            content=[TextContent(type="text", text="hello world")],
        ),
    )


@pytest.fixture
def session_info():
    return SessionInfo(
        session_id=FULL_UUID,
        path=Path("fake.jsonl"),
        title="test session",
        first_timestamp=TS,
        message_count=10,
        stats=TranscriptStats(),
    )


@contextmanager
def patch_session_corpus(sessions):
    """Stub the corpus discovery + promotion seam with pre-built SessionInfos.

    Tool-layer tests hand this the SessionInfos they want the corpus to
    contain; Corpus.discover serves matching filename refs and
    SessionInfo.load promotes a ref back to its pre-built SessionInfo — no
    filesystem, no parse. Patched on the shared classes, so it covers every
    import site (mcp_server, search).
    """
    from cc_explorer.corpus import Corpus, SessionRef
    from cc_explorer.utils import PrefixId

    refs = [
        SessionRef(
            session_id=PrefixId(str(s.session_id.full if isinstance(s.session_id, PrefixId) else s.session_id)),
            path=s.path,
            project_path=s.project_path or "/fake",
            worktree=s.worktree,
        )
        for s in sessions
    ]
    by_path = {s.path: s for s in sessions}

    with patch.object(
        Corpus, "discover", classmethod(lambda cls, projects=None: Corpus(list(refs)))
    ), patch.object(
        SessionInfo, "load", classmethod(lambda cls, ref: by_path.get(ref.path))
    ):
        yield
