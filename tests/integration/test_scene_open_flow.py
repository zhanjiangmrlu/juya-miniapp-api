from datetime import UTC, datetime

import httpx
import pytest

from juya_miniapp_api.integrations.admin_api.client import AdminApiClient
from juya_miniapp_api.modules.learning.access_service import AccessService, InMemoryOpenHistory

NOW = datetime(2026, 9, 28, 21, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_local_open_history_is_written_only_after_authorized_scene_response() -> None:
    mode = "success"

    async def handler(request: httpx.Request) -> httpx.Response:
        if mode == "failure":
            raise httpx.ConnectError("admin unavailable", request=request)
        return httpx.Response(
            200,
            json={
                "access": "LIMITED",
                "sources": ["LIMITED:grant-1"],
                "earliest_expires_at": "2026-10-01T00:00:00Z",
                "activated_at": "2026-09-28T21:30:00Z",
                "scene": {
                    "scene_id": "scene-1",
                    "revision_id": "rev-1",
                    "content_version": 1,
                    "content": {"title_zh": "完整场景"},
                },
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://admin-api")
    client = AdminApiClient(http, secret=b"s" * 32, clock=lambda: NOW)
    history = InMemoryOpenHistory()
    service = AccessService(client, history)
    try:
        opened = await service.open_scene("user-1", "scene-1", "open-1", NOW)
        mode = "failure"
        pending = await service.open_scene("user-1", "scene-2", "open-2", NOW)
    finally:
        await http.aclose()

    assert opened.authorization_pending is False
    assert opened.scene is not None
    assert pending.authorization_pending is True
    assert pending.scene is None
    assert [item[1] for item in history.opened] == ["scene-1"]
