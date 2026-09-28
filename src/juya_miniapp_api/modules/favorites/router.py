from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field

from juya_miniapp_api.modules.favorites.domain import FavoriteEntry
from juya_miniapp_api.modules.favorites.repository import SQLAlchemyFavoriteRepository
from juya_miniapp_api.modules.favorites.service import FavoriteService
from juya_miniapp_api.modules.users.router import UserDependency


class FavoriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_type: str
    text: str = Field(min_length=1, max_length=255)
    entry_stable_id: str = Field(min_length=1, max_length=64)
    scene_id: str = Field(min_length=1, max_length=64)
    sentence_snapshot: str = Field(min_length=1)
    source_locator: str = Field(min_length=1, max_length=255)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card_ids: list[str]


def _favorite(value: FavoriteEntry) -> dict[str, object]:
    return {
        "id": value.public_id,
        "entry_type": value.entry_type,
        "normalized_key": value.normalized_key,
        "entry_stable_id": value.entry_stable_id,
        "favorited_at": value.favorited_at,
        "last_reviewed_at": value.last_reviewed_at,
        "sources": [
            {
                "scene_id": item.scene_id,
                "sentence_snapshot": item.sentence_snapshot,
                "source_locator": item.source_locator,
                "original_link": item.original_link,
            }
            for item in value.sources
        ],
    }


def create_favorites_router(
    service: FavoriteService,
    repository: SQLAlchemyFavoriteRepository,
    *,
    user_dependency: UserDependency,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["favorites"])

    @router.get("/favorites")
    async def favorites(
        user_id: Annotated[str, Depends(user_dependency)],
        cursor: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> dict[str, object]:
        items = await repository.list_favorites(user_id, after_id=cursor, limit=limit + 1)
        visible = items[:limit]
        return {
            "items": [_favorite(item) for item in visible],
            "next_cursor": visible[-1].public_id if len(items) > limit else None,
            "has_more": len(items) > limit,
        }

    @router.post("/favorites", status_code=201)
    async def create_favorite(
        payload: FavoriteRequest,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        return _favorite(
            await service.favorite(
                user_id,
                payload.entry_type,
                payload.text,
                payload.entry_stable_id,
                payload.scene_id,
                payload.sentence_snapshot,
                payload.source_locator,
                clock(),
            )
        )

    @router.get("/favorites/{favorite_id}")
    async def favorite_detail(
        favorite_id: str,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        return _favorite(await service.detail(user_id, favorite_id, accessible_scene_ids=set()))

    @router.delete("/favorites/{favorite_id}", status_code=204)
    async def delete_favorite(
        favorite_id: str,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> None:
        await service.delete(user_id, favorite_id)

    @router.post("/reviews", status_code=201)
    async def create_review(
        payload: ReviewRequest,
        user_id: Annotated[str, Depends(user_dependency)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, object]:
        review = await service.create_review(user_id, payload.card_ids, idempotency_key, clock())
        return {
            "id": review.id,
            "card_count": review.card_count,
            "started_at": review.started_at,
        }

    @router.post("/reviews/{review_id}/complete")
    async def complete_review(
        review_id: str,
        user_id: Annotated[str, Depends(user_dependency)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, object]:
        result = await service.complete_review(user_id, review_id, idempotency_key, clock())
        return {
            "id": result.session.id,
            "created": result.created,
            "completed_at": result.session.completed_at,
            "checkin_date": result.checkin_date,
        }

    return router
