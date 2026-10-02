import pytest
from httpx import ASGITransport, AsyncClient

from juya_miniapp_api.infrastructure.config import Settings
from juya_miniapp_api.integrations.admin_api.schemas import SceneOpenResult
from juya_miniapp_api.main import create_app


@pytest.mark.asyncio
async def test_local_published_scene_and_preview_follow_production_shapes() -> None:
    app = create_app(Settings(environment="local", local_dev_mode=True))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        full = await client.post("/api/v1/scenes/scene-coffee-shop/open")
        preview = await client.post("/api/v1/scenes/scene-weekend-trip/open")
    parsed = SceneOpenResult.model_validate(full.json())
    assert parsed.scene is not None
    content = full.json()["scene"]["content"]
    assert len(content["dialogue"]) == 5
    assert all(
        row["audio_version_id"] == content["audio"]["version_id"] for row in content["dialogue"]
    )
    assert content["dialogue"][0]["clickable_spans"][0]["entry_id"] == "word-latte"
    SceneOpenResult.model_validate(preview.json())
    assert "content" not in preview.json()["scene"]


@pytest.mark.asyncio
async def test_local_resources_and_entries_require_matching_revision() -> None:
    app = create_app(Settings(environment="local", local_dev_mode=True))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        opened = (await client.post("/api/v1/scenes/scene-coffee-shop/open")).json()["scene"]
        revision = opened["revision_id"]
        entry = await client.get(
            f"/api/v1/scenes/scene-coffee-shop/entries/word-latte?revision_id={revision}&entry_version=1&source_locator=sentence:sentence-1:entry:word-latte"
        )
        signed = await client.get(
            f"/api/v1/scenes/scene-coffee-shop/resources/coffee-audio/signed-url?revision_id={revision}"
        )
        stale = await client.get(
            "/api/v1/scenes/scene-coffee-shop/resources/coffee-audio/signed-url?revision_id=old"
        )
        denied = await client.get(
            f"/api/v1/scenes/scene-weekend-trip/resources/coffee-audio/signed-url?revision_id={revision}"
        )
    assert entry.status_code == 200
    assert "latte" in entry.json()["sentence_snapshot"]
    assert signed.status_code == 200
    assert signed.headers["cache-control"] == "private, no-store"
    assert stale.status_code == 409
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_local_contact_prompt_exposure_is_idempotent() -> None:
    app = create_app(Settings(environment="local", local_dev_mode=True))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/api/v1/me/contact/prompt-exposures", headers={"Idempotency-Key": "once"}
        )
        repeated = await client.post(
            "/api/v1/me/contact/prompt-exposures", headers={"Idempotency-Key": "once"}
        )
    assert first.json() == {"created": True}
    assert repeated.json() == {"created": False}
