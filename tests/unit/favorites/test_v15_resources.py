from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from juya_miniapp_api.modules.favorites.resource_router import create_favorite_resource_router
from juya_miniapp_api.modules.favorites.resources import FavoriteResourceService
from juya_miniapp_api.modules.favorites.snapshots import (
    ConfirmedSnapshot,
    FavoriteSnapshot,
    InMemorySnapshotRepository,
    SnapshotReference,
    SnapshotService,
)
from juya_miniapp_api.shared.errors import AppError, install_error_handlers

NOW = datetime(2026, 10, 11, tzinfo=UTC)
URL = "https://private.example/fixed.png?signature=test"


def fixture():
    row = FavoriteSnapshot(
        favorite_id="favorite",
        active=True,
        snapshot_version=1,
        snapshot_revision_id="snapshot-r1",
        saved_content_version=1,
        favorited_at=NOW,
        data=ConfirmedSnapshot(
            reference=SnapshotReference(
                content_type="dialogue",
                content_id="scene",
                content_version=1,
                entry_type="word",
                entry_id="entry",
                sense_id="sense",
                source_id="source",
            ),
            revision_id="content-r1",
            english="cup",
            meaning_zh="杯子",
            source_sentence="a cup",
            source_title="title",
            source_locator={},
            image_asset_id="image",
        ),
    )
    repository = InMemorySnapshotRepository()
    repository.rows["owner", row.data.reference.identity()] = row
    snapshots = SnapshotService(repository, AsyncMock())
    client = AsyncMock()
    client.review_resource.return_value = {
        "target_id": "image",
        "asset_id": "image",
        "resource_version": None,
        "url": URL,
        "expires_at": (NOW + timedelta(seconds=60)).isoformat(),
        "role": "WORD_IMAGE",
    }
    service = FavoriteResourceService(snapshots, client, clock=lambda: NOW)
    return service, repository, row, client


@pytest.mark.asyncio
async def test_owned_active_word_image_delegates_only_fixed_favorite_reference():
    service, _, _, client = fixture()
    result = await service.signed_url("owner", "favorite", "image")
    assert result.resource_id == "image" and result.url == URL
    assert result.expires_at == NOW + timedelta(seconds=60)
    client.review_resource.assert_awaited_once_with("owner", "favorite", "image", None)


@pytest.mark.asyncio
async def test_fixed_snapshot_needs_no_source_port_and_rechecks_expiry_after_wait():
    service, repo, row, client = fixture()
    # Fixed historical content is deliberately not checked against any current content port.
    repo.rows["owner", row.data.reference.identity()] = row.model_copy(
        update={"saved_content_version": 1}
    )
    assert (await service.signed_url("owner", "favorite", "image")).url == URL
    current = NOW
    service.clock = lambda: current

    async def delayed(*args):
        nonlocal current
        current = NOW + timedelta(seconds=61)
        return client.review_resource.return_value

    client.review_resource.side_effect = delayed
    with pytest.raises(AppError) as error:
        await service.signed_url("owner", "favorite", "image")
    assert error.value.code == "FAVORITE_RESOURCE_CONTRACT_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["other-user", "cancelled", "wrong-target", "no-image", "chunk"])
async def test_denied_reference_never_calls_upstream(case):
    service, repo, row, client = fixture()
    user, target = "owner", "image"
    if case == "other-user":
        user = "other"
    elif case == "wrong-target":
        target = "other-image"
    else:
        update = {"active": False} if case == "cancelled" else {}
        if case in {"no-image", "chunk"}:
            data = row.data.model_copy(update={"image_asset_id": None})
            if case == "chunk":
                data = data.model_copy(
                    update={"reference": data.reference.model_copy(update={"entry_type": "chunk"})}
                )
            update["data"] = data
        repo.rows["owner", row.data.reference.identity()] = row.model_copy(update=update)
    with pytest.raises(AppError) as error:
        await service.signed_url(user, "favorite", target)
    assert error.value.status_code in (403, 404)
    client.review_resource.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["cancel", "recollect"])
async def test_inflight_signature_cannot_survive_cancellation_or_snapshot_revision_change(change):
    service, repo, row, client = fixture()

    async def sign(*args):
        update = (
            {"active": False}
            if change == "cancel"
            else {"snapshot_version": 2, "snapshot_revision_id": "snapshot-r2"}
        )
        repo.rows["owner", row.data.reference.identity()] = row.model_copy(update=update)
        return {"target_id": "image", "url": URL, "expires_at": NOW + timedelta(seconds=60)}

    client.review_resource.side_effect = sign
    with pytest.raises(AppError) as error:
        await service.signed_url("owner", "favorite", "image")
    assert error.value.status_code in (404, 409)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"target_id": "other"},
        {"asset_id": "other"},
        {"resource_version": "audio-v1"},
        {"role": "WHOLE"},
        {"url": "javascript:alert(1)"},
        {"url": "https://user:password@example/image"},
        {"expires_at": NOW},
        {"expires_at": NOW.replace(tzinfo=None)},
        {"expires_at": "invalid"},
    ],
)
async def test_upstream_result_must_match_fixed_image_and_have_valid_future_expiry(change):
    service, _, _, client = fixture()
    client.review_resource.return_value.update(change)
    with pytest.raises(AppError) as error:
        await service.signed_url("owner", "favorite", "image")
    assert error.value.code == "FAVORITE_RESOURCE_CONTRACT_INVALID"


def test_http_route_is_no_store_and_requires_authenticated_owner():
    service, _, _, _ = fixture()

    async def owner():
        return "owner"

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(create_favorite_resource_router(service, user_dependency=owner))
    client = TestClient(app)
    response = client.get("/api/v1/favorite-snapshots/favorite/resources/image/signed-url")
    assert response.status_code == 200
    assert response.json()["resource_id"] == "image" and response.json()["url"] == URL
    assert response.headers["cache-control"] == "private, no-store"
    assert (
        client.get("/api/v1/favorite-snapshots/favorite/resources/other/signed-url").status_code
        == 403
    )

    async def unauthenticated():
        raise AppError("AUTH_REQUIRED", "请登录", 401)

    app.dependency_overrides[owner] = unauthenticated
    assert (
        client.get("/api/v1/favorite-snapshots/favorite/resources/image/signed-url").status_code
        == 401
    )
