from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from juya_miniapp_api.modules.messages.domain import InboxMessage
from juya_miniapp_api.modules.messages.service import MessageService
from juya_miniapp_api.modules.users.router import UserDependency


def serialize_message(item: InboxMessage) -> dict[str, object]:
    return {
        "id": item.id,
        "type": item.message_type,
        "title": item.title,
        "summary": item.summary,
        "related_type": item.related_type,
        "related_id": item.related_id,
        "created_at": item.created_at,
        "read_at": item.read_at,
    }


def create_messages_router(
    service: MessageService,
    *,
    user_dependency: UserDependency,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/messages", tags=["messages"])

    @router.get("")
    async def messages(
        user_id: Annotated[str, Depends(user_dependency)],
        cursor: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> dict[str, object]:
        page = await service.list_messages(user_id, cursor=cursor, limit=limit)
        return {
            "items": [serialize_message(item) for item in page.items],
            "next_cursor": page.next_cursor,
            "has_more": page.has_more,
        }

    @router.post("/{message_id}/read")
    async def read_message(
        message_id: str,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        return serialize_message(await service.mark_read(user_id, message_id, clock()))

    return router
