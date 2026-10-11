from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Header, Query, Response

from juya_miniapp_api.integrations.admin_api.v15_content import SourceResolution
from juya_miniapp_api.modules.favorites.snapshots import (
    EntryType,
    FavoriteSnapshot,
    SnapshotReference,
    SnapshotService,
)
from juya_miniapp_api.modules.users.router import UserDependency
from juya_miniapp_api.shared.errors import AppError


class SourceResolver(Protocol):
    async def source_resolution(self, user_id: str, favorite_id: str) -> SourceResolution: ...


def public_snapshot(row: FavoriteSnapshot) -> dict[str, object]:
    data = row.data
    return {
        **data.reference.model_dump(),
        "schema_version": 2,
        "favorite_id": row.favorite_id,
        "active": row.active,
        "snapshot_version": row.snapshot_version,
        "saved_content_version": row.saved_content_version,
        "english": data.english,
        "phonetic": data.phonetic,
        "meaning_zh": data.meaning_zh,
        "explanation": data.explanation,
        "source_sentence": data.source_sentence,
        "source_title": data.source_title,
        "source_locator": data.source_locator,
        "image_asset_id": data.image_asset_id,
        "pronunciation": data.pronunciation.model_dump() if data.pronunciation else None,
        "favorited_at": row.favorited_at,
        "last_reviewed_at": row.last_reviewed_at,
    }


def create_snapshot_router(
    service: SnapshotService,
    *,
    user_dependency: UserDependency,
    resolver: SourceResolver | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/favorite-snapshots", tags=["favorite-snapshots"])

    @router.post("", status_code=201)
    async def create(
        payload: SnapshotReference,
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
        key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        return public_snapshot(await service.create(user_id, payload, key, clock()))

    @router.get("")
    async def listing(
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
        entry_type: EntryType | None = None,
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        page = await service.page(user_id, entry_type, cursor, limit)
        return {
            "items": [public_snapshot(row) for row in page.items],
            "next_cursor": page.next_cursor,
            "has_more": page.has_more,
        }

    @router.get("/{favorite_id}/source-resolution")
    async def source(
        favorite_id: str, response: Response, user_id: Annotated[str, Depends(user_dependency)]
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        row = await service.detail(user_id, favorite_id)
        if resolver is None:
            raise AppError("ADMIN_API_UNAVAILABLE", "内容与权益服务暂时不可用", 503)
        result = await resolver.source_resolution(user_id, favorite_id)
        if result.saved_content_version != row.saved_content_version:
            raise AppError("CONTENT_CONTRACT_INVALID", "来源版本不一致", 503)
        return {
            **result.model_dump(),
            "content_type": row.data.reference.content_type,
            "content_id": row.data.reference.content_id,
        }

    @router.get("/{favorite_id}")
    async def detail(
        favorite_id: str, response: Response, user_id: Annotated[str, Depends(user_dependency)]
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        return public_snapshot(await service.detail(user_id, favorite_id))

    @router.delete("/{favorite_id}")
    async def cancel(
        favorite_id: str,
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
        key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        row = await service.cancel(user_id, favorite_id, key, clock())
        return {
            "favorite_id": row.favorite_id,
            "active": row.active,
            "snapshot_version": row.snapshot_version,
        }

    return router
