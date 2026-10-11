from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from juya_miniapp_api.integrations.admin_api.v15_content import SourceResolution
from juya_miniapp_api.modules.favorites.snapshot_router import create_snapshot_router
from juya_miniapp_api.modules.favorites.snapshots import (
    ConfirmedSnapshot,
    FavoriteSnapshot,
    InMemorySnapshotRepository,
    SnapshotReference,
    SnapshotService,
)

NOW = datetime(2026, 10, 11, tzinfo=UTC)


class Authority:
    async def confirm_snapshot(self, user_id, reference):
        raise AssertionError("Legacy read test must never confirm current content")


class Resolver:
    async def source_resolution(self, user_id, favorite_id):
        return SourceResolution(
            saved_content_version=None,
            target_content_version=None,
            status="NO_ACCESS",
            safe_summary={"title_zh": "Known saved title"},
        )


def app_with_legacy_snapshot() -> FastAPI:
    repository = InMemorySnapshotRepository()
    reference = SnapshotReference(
        content_type="SCENE",
        content_id="missing-scene",
        content_version=None,
        entry_type="word",
        entry_id="legacy-entry",
        sense_id="legacy-sense",
        source_id="legacy-source",
    )
    row = FavoriteSnapshot(
        favorite_id="legacy-favorite",
        active=True,
        snapshot_version=1,
        saved_content_version=None,
        favorited_at=NOW,
        data=ConfirmedSnapshot(
            reference=reference,
            revision_id=None,
            english="saved word",
            meaning_zh="",
            source_sentence="Saved source",
            source_title="Known saved title",
            source_locator={},
            legacy_incomplete=True,
            missing_fields=["meaning_zh", "saved_content_version", "revision_id"],
        ),
    )
    repository.rows["owner", reference.identity()] = row
    service = SnapshotService(repository, Authority())

    async def owner() -> str:
        return "owner"

    app = FastAPI()
    app.include_router(
        create_snapshot_router(
            service, user_dependency=owner, resolver=Resolver(), clock=lambda: NOW
        )
    )
    return app


def test_snapshot_openapi_describes_flat_nullable_legacy_and_typed_pages() -> None:
    schema = app_with_legacy_snapshot().openapi()
    paths = schema["paths"]
    schemas = schema["components"]["schemas"]
    for method, path, status in [
        ("get", "/api/v1/favorite-snapshots/{favorite_id}", "200"),
        ("post", "/api/v1/favorite-snapshots", "201"),
    ]:
        shape = paths[path][method]["responses"][status]["content"]["application/json"]["schema"]
        assert shape.get("$ref") == "#/components/schemas/FavoriteSnapshotResponse"
    flat = schemas["FavoriteSnapshotResponse"]
    assert flat["additionalProperties"] is False
    assert "data" not in flat["properties"] and "credential" not in flat["properties"]
    for field in ["content_version", "saved_content_version", "revision_id"]:
        assert {"type": "null"} in flat["properties"][field]["anyOf"]
    assert flat["properties"]["legacy_incomplete"]["type"] == "boolean"
    assert flat["properties"]["missing_fields"]["items"]["type"] == "string"
    page = paths["/api/v1/favorite-snapshots"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert page["$ref"] == "#/components/schemas/FavoriteSnapshotPageResponse"
    assert (
        schemas["FavoriteSnapshotPageResponse"]["properties"]["items"]["items"]["$ref"]
        == "#/components/schemas/FavoriteSnapshotResponse"
    )


def test_legacy_read_source_and_cancel_preserve_nullable_versions_without_credentials() -> None:
    with TestClient(app_with_legacy_snapshot()) as client:
        detail = client.get("/api/v1/favorite-snapshots/legacy-favorite")
        assert detail.status_code == 200
        row = detail.json()
        assert row["content_version"] is None and row["saved_content_version"] is None
        assert row["revision_id"] is None
        assert row["legacy_incomplete"] and row["meaning_zh"] == ""
        assert "revision_id" in row["missing_fields"]
        assert "data" not in row and "credential" not in row
        assert client.get("/api/v1/favorite-snapshots").json()["items"] == [row]
        source = client.get("/api/v1/favorite-snapshots/legacy-favorite/source-resolution")
        assert source.json() == {
            "saved_content_version": None,
            "target_content_version": None,
            "status": "NO_ACCESS",
            "locator": None,
            "safe_summary": {"title_zh": "Known saved title"},
            "content_type": "SCENE",
            "content_id": "missing-scene",
        }
        canceled = client.delete(
            "/api/v1/favorite-snapshots/legacy-favorite", headers={"Idempotency-Key": "cancel"}
        )
        assert canceled.json() == {
            "favorite_id": "legacy-favorite",
            "active": False,
            "snapshot_version": 1,
        }
        assert canceled.headers["Cache-Control"] == "no-store"


def test_snapshot_response_models_reject_unexpected_internal_fields() -> None:
    from pydantic import ValidationError

    from juya_miniapp_api.modules.favorites.public_dto import FavoriteSnapshotResponse

    with TestClient(app_with_legacy_snapshot()) as client:
        row = client.get("/api/v1/favorite-snapshots/legacy-favorite").json()
    with pytest.raises(ValidationError):
        FavoriteSnapshotResponse.model_validate({**row, "_confirmation_credential": "private"})
