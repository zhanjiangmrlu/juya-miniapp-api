from datetime import UTC, datetime, timedelta

import pytest

from juya_miniapp_api.modules.learning.domain import ReadingPosition
from juya_miniapp_api.modules.learning.repository import InMemoryLearningRepository
from juya_miniapp_api.modules.learning.service import LearningService

NOW = datetime(2026, 9, 28, 18, 0, tzinfo=UTC)
USER_ID = "01K00000000000000000000001"


@pytest.mark.asyncio
async def test_older_client_sequence_cannot_overwrite_newer_position() -> None:
    service = LearningService(InMemoryLearningRepository())

    newer = await service.save_progress(USER_ID, "scene-1", 5, ReadingPosition("entry-5", 100), NOW)
    stale = await service.save_progress(
        USER_ID,
        "scene-1",
        4,
        ReadingPosition("entry-4", 20),
        NOW + timedelta(minutes=1),
    )

    assert newer.client_sequence == 5
    assert stale.client_sequence == 5
    assert stale.position == ReadingPosition("entry-5", 100)


@pytest.mark.asyncio
async def test_repeated_completion_creates_one_event_and_checkin() -> None:
    repository = InMemoryLearningRepository()
    service = LearningService(repository)

    first = await service.complete(USER_ID, "scene-1", "complete-1", NOW)
    repeated = await service.complete(USER_ID, "scene-1", "complete-2", NOW)

    assert first.created is True
    assert repeated.created is False
    assert len(repository.completion_events) == 1
    assert len(repository.checkins) == 1
