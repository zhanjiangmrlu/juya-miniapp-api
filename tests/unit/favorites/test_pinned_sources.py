from datetime import UTC, datetime

import pytest

from juya_miniapp_api.modules.favorites.repository import InMemoryFavoriteRepository
from juya_miniapp_api.modules.favorites.service import FavoriteService


@pytest.mark.asyncio
async def test_same_word_retains_each_published_source_and_optional_pronunciation() -> None:
    repo = InMemoryFavoriteRepository()
    service = FavoriteService(repo)
    now = datetime.now(UTC)
    for revision, version in (("rev-1", 1), ("rev-2", 2)):
        result = await service.favorite(
            "user",
            "VOCABULARY",
            "hello",
            "entry",
            "scene",
            "Hello there",
            "sentence:0:5",
            now,
            revision_id=revision,
            entry_version=version,
            entry_snapshot={"english": "hello", "audio_version_id": None},
        )
    assert len(result.sources) == 2
    assert {source.revision_id for source in result.sources} == {"rev-1", "rev-2"}
    detail = await service.detail("user", result.public_id, accessible_scene_ids={"scene"})
    assert detail.sources[0].entry_snapshot["audio_version_id"] is None
    assert "revision_id=rev-1" in detail.sources[0].original_link
