"""Shared test fixtures."""

from datetime import datetime, timezone

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
        path="fake.jsonl",
        title="test session",
        first_timestamp=TS,
        message_count=10,
        stats=TranscriptStats(),
    )
