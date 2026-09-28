from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from juya_miniapp_api.infrastructure.security.service_hmac import ServicePrincipal
from juya_miniapp_api.modules.messages.router import serialize_message
from juya_miniapp_api.modules.messages.service import MessageService


class InternalMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=128)
    message_type: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=500)
    related_type: str | None = Field(default=None, max_length=64)
    related_id: str | None = Field(default=None, max_length=64)


ServiceDependency = Callable[..., Awaitable[ServicePrincipal]]


def create_internal_messages_router(
    service: MessageService,
    *,
    current_service: ServiceDependency,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(prefix="/internal/v1", tags=["internal-messages"])

    @router.post("/users/{user_id}/messages", status_code=201)
    async def create_message(
        user_id: str,
        payload: InternalMessageRequest,
        response: Response,
        _principal: Annotated[ServicePrincipal, Depends(current_service)],
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        item = await service.create(
            user_id,
            payload.event_id,
            payload.message_type,
            payload.title,
            payload.summary,
            payload.related_type,
            payload.related_id,
            clock(),
        )
        return serialize_message(item)

    return router
