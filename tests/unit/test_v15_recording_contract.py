import pytest
from pydantic import ValidationError

from juya_miniapp_api.integrations.admin_api.v15_content import SceneDetail


@pytest.mark.parametrize("enabled", [True, False])
def test_authoritative_scene_recording_switch_survives_public_dto(enabled):
    detail = SceneDetail.model_validate(
        {
            "recording_enabled": enabled,
            "content_id": "scene",
            "content_type": "dialogue",
            "content_version": 1,
            "revision_id": "revision",
            "title_en": "Title",
            "title_zh": "标题",
        }
    )
    assert detail.model_dump()["recording_enabled"] is enabled


def test_recording_switch_rejects_coerced_true():
    with pytest.raises(ValidationError):
        SceneDetail.model_validate(
            {
                "recording_enabled": "true",
                "content_id": "scene",
                "content_type": "dialogue",
                "content_version": 1,
                "revision_id": "revision",
                "title_en": "Title",
                "title_zh": "标题",
            }
        )
