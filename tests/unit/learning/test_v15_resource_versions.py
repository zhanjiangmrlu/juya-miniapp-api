from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from juya_miniapp_api.integrations.admin_api.v15_content import SceneDetail
from juya_miniapp_api.modules.learning.v15_authority import V15SceneAuthority
from juya_miniapp_api.shared.errors import AppError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target,version",
    [("whole", "whole-v2"), ("slice", "slice-v2"), ("word", "word-v2"), ("image", None)],
)
async def test_current_scene_resource_delegates_exact_fixed_version(target, version):
    now = datetime.now(UTC)
    client = AsyncMock()
    client.resource.return_value = {
        "target_id": target,
        "url": "https://media.invalid/test",
        "expires_at": (now + timedelta(seconds=60)).isoformat(),
    }
    authority = V15SceneAuthority(None, client)
    authority.open_scene = AsyncMock(
        return_value=SceneDetail(
            content_id="scene",
            content_type="dialogue",
            content_version=2,
            revision_id="rev2",
            title_en="A",
            title_zh="甲",
            cover_asset_id="image",
            whole_audio={"target_id": "whole", "resource_version": "whole-v2", "duration_ms": 1000},
            dialogue=[
                {
                    "sentence_id": "sentence",
                    "speaker": "A",
                    "english": "Hello",
                    "chinese": "你好",
                    "position": 1,
                    "slice_target_id": "slice",
                    "slice_version": "slice-v2",
                }
            ],
            words=[
                {
                    "entry_id": "entry",
                    "entry_type": "word",
                    "sense_id": "sense",
                    "source_id": "source",
                    "english": "Hello",
                    "meaning_zh": "你好",
                    "source_sentence": "Hello",
                    "pronunciation": {"target_id": "word", "resource_version": "word-v2"},
                }
            ],
        )
    )
    await authority.signed_resource("user", "scene", target, "rev2", now)
    client.resource.assert_awaited_once_with("user", "dialogue", "scene", target, version)
    client.resource.reset_mock()
    with pytest.raises(AppError) as unknown:
        await authority.signed_resource("user", "scene", "other", "rev2", now)
    assert unknown.value.status_code == 403
    client.resource.assert_not_awaited()
    with pytest.raises(AppError) as historical:
        await authority.signed_resource("user", "scene", target, "rev1", now)
    assert historical.value.status_code == 409
