from datetime import UTC, datetime

import pytest

from juya_miniapp_api.modules.favorites.repository import InMemoryFavoriteRepository
from juya_miniapp_api.modules.favorites.service import (
    FavoriteService,
    normalize_favorite_key,
)

NOW = datetime(2026, 9, 28, 22, 0, tzinfo=UTC)
USER_ID = "01K00000000000000000000001"


def test_normalization_is_unicode_case_and_space_only_without_lemmatization() -> None:
    assert normalize_favorite_key("  \uff28\uff45\uff4c\uff4c\uff4f   WORLD  ") == "hello world"
    assert normalize_favorite_key("Running") == "running"
    assert normalize_favorite_key("run") == "run"
    assert normalize_favorite_key("Running") != normalize_favorite_key("run")


@pytest.mark.asyncio
async def test_same_normalized_entry_merges_sources_and_keeps_snapshots() -> None:
    repository = InMemoryFavoriteRepository()
    service = FavoriteService(repository)

    first = await service.favorite(
        USER_ID,
        "VOCABULARY",
        "Hello  World",
        "entry-1",
        "scene-1",
        "Hello world from scene one.",
        "sentence-1",
        NOW,
    )
    merged = await service.favorite(
        USER_ID,
        "VOCABULARY",
        " hello world ",
        "entry-1",
        "scene-2",
        "Hello world from scene two.",
        "sentence-2",
        NOW,
    )

    assert first.public_id == merged.public_id
    assert len(merged.sources) == 2
    restricted = await service.detail(USER_ID, merged.public_id, accessible_scene_ids=set())
    assert [item.sentence_snapshot for item in restricted.sources] == [
        "Hello world from scene one.",
        "Hello world from scene two.",
    ]
    assert all(item.original_link is None for item in restricted.sources)
    accessible = await service.detail(USER_ID, merged.public_id, accessible_scene_ids={"scene-2"})
    assert accessible.sources[0].original_link is None
    assert accessible.sources[1].original_link == "/scenes/scene-2#sentence-2"


@pytest.mark.asyncio
async def test_delete_cascades_sources_and_review_has_no_card_limit() -> None:
    repository = InMemoryFavoriteRepository()
    service = FavoriteService(repository)
    favorite = await service.favorite(
        USER_ID,
        "PHRASE",
        "Good morning",
        "entry-2",
        "scene-1",
        "Good morning, everyone.",
        "sentence-1",
        NOW,
    )
    cards = [
        await service.favorite(
            USER_ID,
            "VOCABULARY",
            f"word {index}",
            f"entry-{index}",
            "scene-1",
            "sentence",
            "sentence-1",
            NOW,
        )
        for index in range(500)
    ]
    review = await service.create_review(
        USER_ID, [card.public_id for card in cards], "review-1", NOW
    )
    first = await service.complete_review(USER_ID, review.id, "done-1", NOW)
    repeated = await service.complete_review(USER_ID, review.id, "done-2", NOW)
    await service.delete(USER_ID, favorite.public_id)
    for card in cards:
        await service.delete(USER_ID, card.public_id)

    assert review.card_count == 500
    assert first.created is True
    assert repeated.created is False
    assert repository.favorites == {}
    assert repository.sources == {}
    assert len(repository.checkins) == 1
