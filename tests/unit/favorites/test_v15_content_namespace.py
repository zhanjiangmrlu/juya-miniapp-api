from datetime import UTC, datetime

import pytest

from juya_miniapp_api.integrations.admin_api.v15_content import SceneDetail
from juya_miniapp_api.modules.favorites.snapshots import (
    ConfirmedSnapshot,
    InMemorySnapshotRepository,
    SnapshotReference,
    SnapshotService,
)
from juya_miniapp_api.modules.learning.detail_aggregation import attach_favorite_states

NOW = datetime(2026, 10, 11, tzinfo=UTC)


def reference(kind="dialogue", content="dialogue-content"):
    return SnapshotReference(
        content_type=kind,
        content_id=content,
        content_version=1,
        entry_type="word",
        entry_id="medium-cup",
        sense_id="cup",
        source_id="cup-source",
    )


class Authority:
    async def confirm_snapshot(self, user_id, ref):
        return ConfirmedSnapshot(
            reference=ref,
            revision_id="revision-" + ref.content_id,
            english="medium",
            phonetic="",
            meaning_zh="中杯",
            explanation="",
            source_sentence="A medium coffee.",
            source_title=ref.content_id,
            source_locator={"sentence_id": "cup-sentence"},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,content_id",
    [
        ("word_card", "word-content"),
        ("dialogue", "another-dialogue"),
        ("word_card", "dialogue-content"),
    ],
)
async def test_other_content_with_same_local_entry_ids_is_not_prefilled_as_favorited(
    kind, content_id
):
    repository = InMemorySnapshotRepository()
    service = SnapshotService(repository, Authority())
    await service.create("owner", reference(), "save-dialogue", NOW)
    detail = SceneDetail.model_validate(
        {
            "content_id": content_id,
            "content_type": kind,
            "content_version": 1,
            "revision_id": "word-r1",
            "title_en": "Word card",
            "title_zh": "词卡",
            "words": [
                {
                    "entry_type": "word",
                    "entry_id": "medium-cup",
                    "sense_id": "cup",
                    "source_id": "cup-source",
                    "english": "medium",
                    "meaning_zh": "中杯",
                    "source_sentence": "A medium coffee.",
                }
            ],
        }
    )
    enriched = await attach_favorite_states("owner", detail, repository)
    assert enriched.words[0].favorite_id is None
    assert enriched.words[0].is_favorited is False


@pytest.mark.asyncio
async def test_other_content_favorite_is_independent_and_cancellation_does_not_cross_content():
    service = SnapshotService(InMemorySnapshotRepository(), Authority())
    first = await service.create("owner", reference(), "save-dialogue", NOW)
    second = await service.create(
        "owner", reference("word_card", "word-content"), "save-word-card", NOW
    )
    assert first.favorite_id != second.favorite_id
    assert second.data.reference.content_id == "word-content"
    await service.cancel("owner", second.favorite_id, "cancel-word-card", NOW)
    assert (await service.detail("owner", first.favorite_id)).active is True
