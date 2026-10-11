from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from juya_miniapp_api.modules.favorites.snapshots import (
    ConfirmedSnapshot,
    InMemorySnapshotRepository,
    SnapshotReference,
    SnapshotService,
)


class Authority:
    async def confirm_snapshot(self, user, ref):
        return ConfirmedSnapshot(
            reference=ref.model_copy(update={"entry_type": "word"}),
            revision_id="r1",
            english="medium",
            meaning_zh="中杯",
            source_title="咖啡店",
            source_sentence="A medium coffee, please.",
            source_locator={"sentence_id": "s1"},
        )


def test_snapshot_routes_reference_only_flat_owned_snapshot_and_cancel() -> None:
    from juya_miniapp_api.modules.favorites.snapshot_router import create_snapshot_router

    async def user() -> str:
        return "user"

    app = FastAPI()
    service = SnapshotService(InMemorySnapshotRepository(), Authority())
    app.include_router(
        create_snapshot_router(
            service, user_dependency=user, clock=lambda: datetime(2026, 10, 11, tzinfo=UTC)
        )
    )
    client = TestClient(app)
    ref = SnapshotReference(
        content_type="dialogue",
        content_id="coffee",
        content_version=1,
        entry_id="medium",
        sense_id="cup",
        source_id="source",
    ).model_dump(exclude={"entry_type"})
    created = client.post(
        "/api/v1/favorite-snapshots", json=ref, headers={"Idempotency-Key": "create"}
    )
    assert created.status_code == 201
    data = created.json()
    assert data["entry_type"] == "word" and data["meaning_zh"] == "中杯"
    assert data["source_sentence"] == "A medium coffee, please."
    assert "data" not in data and "credential" not in data
    assert (
        client.post(
            "/api/v1/favorite-snapshots",
            json={**ref, "english": "invented"},
            headers={"Idempotency-Key": "bad"},
        ).status_code
        == 422
    )
    assert client.get("/api/v1/favorite-snapshots").json()["items"] == [data]
    identity = data["favorite_id"]
    canceled = client.delete(
        "/api/v1/favorite-snapshots/" + identity, headers={"Idempotency-Key": "cancel"}
    )
    assert canceled.json() == {"favorite_id": identity, "active": False, "snapshot_version": 1}
    assert client.get("/api/v1/favorite-snapshots").json()["items"] == []
