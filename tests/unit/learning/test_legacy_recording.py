from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from juya_miniapp_api.integrations.admin_api.client import AdminApiUnavailable
from juya_miniapp_api.integrations.admin_api.schemas import PublishedScene, SceneOpenResult
from juya_miniapp_api.integrations.admin_api.v15_client import AdminV15Client
from juya_miniapp_api.modules.learning.access_service import AccessService, InMemoryOpenHistory

NOW = datetime.now(UTC)


class LegacyClient:
    async def open_scene(self, *args):
        return SceneOpenResult(
            access="OPEN",
            scene=PublishedScene.model_validate(
                {"scene_id": "old", "revision_id": "r1", "content_version": 1, "content": {}}
            ),
        )


class Features:
    def __init__(self, enabled):
        self.enabled = enabled
        self.calls = 0

    async def learning_features(self):
        self.calls += 1
        if self.enabled is None:
            raise AdminApiUnavailable()
        return SimpleNamespace(recording_enabled=self.enabled)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False, None])
async def test_legacy_authorized_recording_uses_live_flag_and_only_flag_fails_closed(enabled):
    history = InMemoryOpenHistory()
    features = Features(enabled)
    service = AccessService(LegacyClient(), history, features=features)
    opened = await service.open_scene("u", "old", "open", NOW)
    assert opened.access == "OPEN" and not opened.authorization_pending
    assert opened.scene.model_dump()["recording_enabled"] is (enabled is True)
    assert opened.scene.content.model_dump()["schema_version"] == 1
    assert features.calls == 1 and len(history.opened) == 1
    features.enabled = False
    reopened = await service.open_scene("u", "old", "same-key", NOW)
    assert reopened.scene.model_dump()["recording_enabled"] is False


@pytest.mark.asyncio
async def test_legacy_without_features_defaults_closed():
    opened = await AccessService(LegacyClient(), InMemoryOpenHistory()).open_scene(
        "u", "old", "open", NOW
    )
    assert opened.scene.model_dump()["recording_enabled"] is False


@pytest.mark.asyncio
async def test_preview_and_pending_do_not_read_or_expose_recording_flag():
    class PreviewClient:
        async def open_scene(self, *args):
            return SceneOpenResult.model_validate(
                {"access": "PREVIEW", "scene": {"public_id": "old", "title": "preview"}}
            )

    features = Features(True)
    result = await AccessService(
        PreviewClient(), InMemoryOpenHistory(), features=features
    ).open_scene("u", "old", "preview", NOW)
    assert features.calls == 0
    assert "recording_enabled" not in result.scene.model_dump()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,valid",
    [
        ({"recording_enabled": True, "version": 2}, True),
        ({"recording_enabled": False, "version": 0}, True),
        ({"recording_enabled": "true", "version": 2}, False),
        ({"recording_enabled": True, "version": True}, False),
        ({"recording_enabled": True, "version": -1}, False),
        ({"version": 2}, False),
    ],
)
async def test_actual_features_consumer_posts_empty_hmac_body_and_rejects_bad_dto(payload, valid):
    def respond(request):
        assert request.method == "POST"
        assert request.url.path == "/internal/v1/learning-features"
        assert request.content == b"{}"
        assert request.headers.get("X-Juya-Signature")
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="http://admin.test"
    ) as http:
        client = AdminV15Client(http, secret=b"isolated-feature-secret")
        if valid:
            result = await client.learning_features()
            assert result.recording_enabled is payload["recording_enabled"]
        else:
            with pytest.raises(AdminApiUnavailable):
                await client.learning_features()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404, 503])
async def test_optional_config_http_failure_keeps_authorized_legacy_reading(status):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                status, json={"code": "FEATURES_UNAVAILABLE", "message": "unavailable"}
            )
        ),
        base_url="http://admin.test",
    ) as http:
        features = AdminV15Client(http, secret=b"isolated-feature-secret")
        history = InMemoryOpenHistory()
        opened = await AccessService(LegacyClient(), history, features=features).open_scene(
            "u", "old", "open", NOW
        )
        assert opened.access == "OPEN" and not opened.authorization_pending
        assert opened.scene.model_dump()["recording_enabled"] is False
        assert len(history.opened) == 1
