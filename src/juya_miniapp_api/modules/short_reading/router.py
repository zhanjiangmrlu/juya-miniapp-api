from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import Field

from juya_miniapp_api.modules.favorites.snapshots import StrictModel
from juya_miniapp_api.modules.short_reading.authority import V15ReadingAuthority
from juya_miniapp_api.modules.short_reading.domain import (
    ReadingDetail,
    ReadingPosition,
    ReadingPreview,
    ReadingProgress,
)
from juya_miniapp_api.modules.short_reading.history import ReadingHistoryPage, SQLReadingHistory
from juya_miniapp_api.modules.short_reading.public_dto import (
    ReadingCompleteResponse,
    ReadingPageResponse,
    ReadingResourceResponse,
)
from juya_miniapp_api.modules.short_reading.service import ShortReadingService
from juya_miniapp_api.modules.users.router import UserDependency


class PositionRequest(ReadingPosition):
    client_sequence: int = Field(ge=1)


class CompleteRequest(StrictModel):
    content_version: int = Field(ge=1)


def create_short_reading_router(
    service: ShortReadingService,
    authority: V15ReadingAuthority,
    *,
    user_dependency: UserDependency,
    history: SQLReadingHistory | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/short-readings", tags=["short-readings"])

    @router.get("/history", response_model=ReadingHistoryPage)
    async def reading_history(
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> ReadingHistoryPage:
        from juya_miniapp_api.shared.errors import AppError

        response.headers["Cache-Control"] = "private, no-store"
        if history is None:
            raise AppError("READING_HISTORY_UNAVAILABLE", "阅读记录暂不可用", 503)
        return await history.page(user_id, cursor, limit, clock())

    @router.get("", response_model=ReadingPageResponse)
    async def listing(
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
        cursor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        return await authority.listing(user_id, cursor, limit)

    @router.get("/{identity}/preview", response_model=ReadingPreview)
    async def preview(
        identity: str, response: Response, user_id: Annotated[str, Depends(user_dependency)]
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        return (await authority.preview(user_id, identity)).model_dump(mode="json")

    @router.post("/{identity}/open", response_model=ReadingDetail)
    async def open_reading(
        identity: str, response: Response, user_id: Annotated[str, Depends(user_dependency)]
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        return (await service.open(user_id, identity, clock())).model_dump(mode="json")

    @router.put("/{identity}/progress", response_model=ReadingProgress)
    async def progress(
        identity: str,
        payload: PositionRequest,
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        return (await service.save(user_id, identity, payload, clock())).model_dump(mode="json")

    @router.post("/{identity}/complete", response_model=ReadingCompleteResponse)
    async def complete(
        identity: str,
        payload: CompleteRequest,
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
        key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        result = await service.complete(user_id, identity, key, payload.content_version, clock())
        return {
            "progress": result.model_dump(mode="json", exclude={"created"}),
            "created": result.created,
        }

    @router.get("/{identity}/resources/{target}/signed-url", response_model=ReadingResourceResponse)
    async def resource(
        identity: str,
        target: str,
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
        revision_id: Annotated[str, Query(min_length=1, max_length=64)],
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        return (await authority.resource(user_id, identity, target, revision_id)).model_dump(
            mode="json"
        )

    return router
