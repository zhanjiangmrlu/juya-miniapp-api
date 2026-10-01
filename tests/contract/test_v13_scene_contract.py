import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from juya_miniapp_api.integrations.admin_api.client import AdminApiClient
from juya_miniapp_api.integrations.admin_api.schemas import SceneOpenResult


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entry_type", "locator"),
    [("VOCABULARY", "vocabulary:entry"), ("PHRASE", "chunks:entry")],
)
async def test_list_entry_favorite_accepts_empty_context_and_pins_authoritative_english(
    entry_type: str,
    locator: str,
) -> None:
    from fastapi import FastAPI

    from juya_miniapp_api.modules.favorites.repository import InMemoryFavoriteRepository
    from juya_miniapp_api.modules.favorites.router import create_favorites_router
    from juya_miniapp_api.modules.favorites.service import FavoriteService
    from juya_miniapp_api.modules.learning.access_service import AccessService, InMemoryOpenHistory

    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "entry_id": "entry",
                "entry_version": 2,
                "entry_type": entry_type,
                "source_locator": locator,
                "revision_id": "revision",
                "scene_id": "scene",
                "english": "Hello there",
                "sentence_snapshot": "",
                "audio_version_id": None,
            },
        )

    async def user() -> str:
        return "user"

    async with httpx.AsyncClient(
        base_url="http://admin", transport=httpx.MockTransport(handler)
    ) as admin_http:
        repository = InMemoryFavoriteRepository()
        service = FavoriteService(
            repository,
            AccessService(AdminApiClient(admin_http, secret=b"s" * 32), InMemoryOpenHistory()),
        )
        app = FastAPI()
        app.include_router(create_favorites_router(service, repository, user_dependency=user))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://mini"
        ) as client:
            response = await client.post(
                "/api/v1/favorites",
                json={
                    "entry_type": entry_type,
                    "text": "untrusted",
                    "entry_stable_id": "entry",
                    "scene_id": "scene",
                    "revision_id": "revision",
                    "entry_version": 2,
                    "sentence_snapshot": "",
                    "source_locator": locator,
                },
            )
    assert response.status_code == 201
    body = response.json()
    assert body["normalized_key"] == "hello there"
    source = body["sources"][0]
    assert source["sentence_snapshot"] == "Hello there"
    assert source["entry_snapshot"]["audio_version_id"] is None
    assert source["revision_id"] == "revision" and source["entry_version"] == 2
    assert source["source_locator"] == locator
    assert json.loads(requests[0].content)["source_locator"] == locator


def test_scene_contract_requires_published_version_and_rejects_private_fields() -> None:
    with pytest.raises(ValidationError):
        SceneOpenResult.model_validate({"access": "OPEN", "scene": {"public_id": "old"}})
    result = SceneOpenResult.model_validate(
        {
            "access": "OPEN",
            "scene": {
                "scene_id": "scene",
                "revision_id": "rev",
                "content_version": 2,
                "content": {
                    "title_en": "Hello",
                    "dialogue": [{"id": "sentence", "english": "Hello"}],
                },
            },
        }
    )
    assert result.scene.content.dialogue[0].id == "sentence"
    with pytest.raises(ValidationError):
        SceneOpenResult.model_validate(
            {
                "access": "OPEN",
                "scene": {
                    "scene_id": "scene",
                    "revision_id": "rev",
                    "content_version": 2,
                    "content": {"object_key": "private"},
                },
            }
        )


@pytest.mark.asyncio
async def test_entry_and_resource_requests_pin_revision_and_locator() -> None:
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "/resources/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "resource_id": "asset",
                    "url": "https://example.test",
                    "expires_at": "2099-01-01T00:00:00Z",
                },
            )
        return httpx.Response(
            200,
            json={
                "entry_id": "entry",
                "entry_version": 2,
                "source_locator": "sentence:0:5",
                "revision_id": "revision",
                "scene_id": "scene",
                "english": "Hello",
            },
        )

    async with httpx.AsyncClient(
        base_url="http://admin", transport=httpx.MockTransport(handler)
    ) as http:
        client = AdminApiClient(http, secret=b"s" * 32, clock=lambda: datetime.now(UTC))
        await client.get_entry("user", "scene", "entry", "revision", 2, "sentence:0:5")
        await client.get_signed_resource("user", "scene", "asset", "revision")
    assert json.loads(requests[0].content) == {
        "user_id": "user",
        "revision_id": "revision",
        "entry_version": 2,
        "source_locator": "sentence:0:5",
    }
    assert requests[1].url.path == "/internal/v1/scenes/scene/resources/asset/signed-url"
    assert json.loads(requests[1].content) == {"user_id": "user", "revision_id": "revision"}


@pytest.mark.asyncio
async def test_preview_has_whitelisted_metadata_and_never_creates_learning_history() -> None:
    from juya_miniapp_api.modules.learning.access_service import AccessService, InMemoryOpenHistory

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access": "PREVIEW",
                "scene": {
                    "public_id": "scene",
                    "title": "Hello",
                    "title_en": "Hello there",
                    "title_zh": "你好",
                    "series": "Starter",
                    "introduction": "Preview",
                    "preview_status": "PREVIEW",
                },
            },
        )

    async with httpx.AsyncClient(
        base_url="http://admin", transport=httpx.MockTransport(handler)
    ) as http:
        history = InMemoryOpenHistory()
        result = await AccessService(AdminApiClient(http, secret=b"s" * 32), history).open_scene(
            "user", "scene", "open", datetime.now(UTC)
        )
    assert result.scene.title == "Hello"
    assert result.scene.title_en == "Hello there"
    assert result.scene.title_zh == "你好"
    assert history.opened == []
