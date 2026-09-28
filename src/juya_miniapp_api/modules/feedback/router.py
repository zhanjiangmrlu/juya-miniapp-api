from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field

from juya_miniapp_api.infrastructure.redis.rate_limit import RateLimiter, enforce_rate_limit
from juya_miniapp_api.integrations.oss.upload import OssUploadService
from juya_miniapp_api.modules.feedback.service import FeedbackService
from juya_miniapp_api.modules.users.router import UserDependency


class FeedbackCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=300)
    source: dict[str, Any] = Field(default_factory=dict)
    screenshots: list[str] = Field(default_factory=list, max_length=1)


class SupplementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=300)


class ResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    reason: str | None = Field(default=None, max_length=300)


def create_feedback_router(
    service: FeedbackService,
    uploads: OssUploadService,
    *,
    user_dependency: UserDependency,
    rate_limiter: RateLimiter | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])

    @router.get("")
    async def list_feedback(
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        return {"items": await service.list_feedback(user_id), "has_more": False}

    @router.post("")
    async def create_feedback(
        payload: FeedbackCreateRequest,
        user_id: Annotated[str, Depends(user_dependency)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, Any]:
        await enforce_rate_limit(
            rate_limiter,
            "feedback",
            user_id,
            limit=5,
            window=timedelta(hours=1),
            now=clock(),
        )
        return await service.create(
            user_id,
            payload.category,
            payload.description,
            payload.source,
            payload.screenshots,
            idempotency_key,
        )

    @router.post("/uploads")
    async def feedback_upload(
        user_id: Annotated[str, Depends(user_dependency)],
        content_type: Annotated[str, Query(alias="content_type")],
    ) -> dict[str, object]:
        await enforce_rate_limit(
            rate_limiter,
            "feedback_upload",
            user_id,
            limit=10,
            window=timedelta(hours=1),
            now=clock(),
        )
        return uploads.create_feedback_upload(user_id, content_type)

    @router.get("/{feedback_id}")
    async def feedback_detail(
        feedback_id: str,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, Any]:
        return await service.detail(user_id, feedback_id)

    @router.post("/{feedback_id}/supplements")
    async def supplement(
        feedback_id: str,
        payload: SupplementRequest,
        user_id: Annotated[str, Depends(user_dependency)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, Any]:
        return await service.supplement(user_id, feedback_id, payload.text, idempotency_key)

    @router.post("/{feedback_id}/resolution")
    async def resolution(
        feedback_id: str,
        payload: ResolutionRequest,
        user_id: Annotated[str, Depends(user_dependency)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, Any]:
        return await service.resolve(
            user_id,
            feedback_id,
            payload.action,
            payload.reason,
            idempotency_key,
        )

    return router
