from datetime import UTC, datetime, timedelta

import pytest

from juya_miniapp_api.integrations.admin_api.client import AdminApiUnavailable
from juya_miniapp_api.integrations.admin_api.schemas import SceneOpenResult, SignedMedia
from juya_miniapp_api.modules.learning.access_service import AccessService, InMemoryOpenHistory
from juya_miniapp_api.shared.errors import AppError

NOW = datetime(2026, 9, 28, 21, 0, tzinfo=UTC)


class FailingAdminClient:
    async def open_scene(
        self, user_id: str, scene_id: str, idempotency_key: str
    ) -> SceneOpenResult:
        del user_id, scene_id, idempotency_key
        raise AdminApiUnavailable()

    async def get_signed_media(self, user_id: str, target_id: str) -> SignedMedia:
        del user_id, target_id
        raise AdminApiUnavailable()


class ExpiredMediaClient(FailingAdminClient):
    async def get_signed_media(self, user_id: str, target_id: str) -> SignedMedia:
        del user_id, target_id
        return SignedMedia(
            target_id="audio-1",
            url="https://oss.example/expired-signature",
            expires_at=NOW - timedelta(seconds=1),
        )


@pytest.mark.asyncio
async def test_admin_failure_returns_pending_without_scene_or_local_open_state() -> None:
    history = InMemoryOpenHistory()
    service = AccessService(FailingAdminClient(), history)

    result = await service.open_scene("user-1", "scene-1", "open-1", NOW)

    assert result.authorization_pending is True
    assert result.scene is None
    assert result.access is None
    assert history.opened == []


@pytest.mark.asyncio
async def test_expired_signed_media_is_rejected_without_returning_url() -> None:
    service = AccessService(ExpiredMediaClient(), InMemoryOpenHistory())

    with pytest.raises(AppError) as error:
        await service.get_signed_media("user-1", "audio-1", NOW)

    assert error.value.code == "SIGNED_MEDIA_EXPIRED"
    assert "expired-signature" not in repr(error.value)
