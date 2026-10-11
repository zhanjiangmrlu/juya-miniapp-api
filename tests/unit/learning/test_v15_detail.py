import pytest
from pydantic import ValidationError

from juya_miniapp_api.integrations.admin_api.v15_content import SceneDetail
from juya_miniapp_api.modules.learning.detail_aggregation import attach_favorite_states


def payload() -> dict:
    return {
        "content_id": "scene",
        "content_type": "word_card",
        "content_version": 2,
        "revision_id": "r2",
        "title_en": "Medium",
        "title_zh": "medium",
        "words": [
            {
                "entry_id": "entry-" + sense,
                "entry_type": "word",
                "sense_id": sense,
                "source_id": "source-" + sense,
                "english": "medium",
                "meaning_zh": meaning,
                "source_sentence": "A medium coffee.",
            }
            for sense, meaning in [("cup", "中杯"), ("size", "中等的")]
        ],
    }


class States:
    calls = 0

    async def states(self, user: str, identities: list[tuple[str, ...]]) -> dict:
        self.calls += 1
        assert user == "user"
        assert len(identities) == 2
        assert identities[0][:2] == ("word_card", "scene")
        return {("word_card", "scene", "word", "entry-cup", "cup", "source-cup"): "favorite-cup"}


@pytest.mark.asyncio
async def test_same_spelling_senses_survive_and_true_state_is_attached_in_one_batch() -> None:
    detail = SceneDetail.model_validate(payload())
    states = States()
    enriched = await attach_favorite_states("user", detail, states)
    assert states.calls == 1
    assert enriched.words[0].favorite_id == "favorite-cup"
    assert enriched.words[0].is_favorited
    assert enriched.words[1].favorite_id is None
    assert not enriched.words[1].is_favorited
    assert not detail.words[0].is_favorited
    assert enriched.content_version == 2


def test_chunk_image_and_mixed_type_identity_rejected() -> None:
    row = payload()
    row["chunks"] = [row["words"][0] | {"entry_type": "chunk", "image_asset_id": "image"}]
    with pytest.raises(ValidationError):
        SceneDetail.model_validate(row)
    row = payload()
    row["chunks"] = [row["words"][0]]
    with pytest.raises(ValidationError):
        SceneDetail.model_validate(row)


def test_schema2_does_not_accept_legacy_clickable_spans_or_signed_urls() -> None:
    row = payload()
    row["words"][0]["audio_url"] = "https://example.test/private"
    with pytest.raises(ValidationError):
        SceneDetail.model_validate(row)
