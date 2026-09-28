from datetime import datetime

from juya_miniapp_api.modules.messages.domain import InboxMessage, MessagePage
from juya_miniapp_api.modules.messages.repository import MessageRepository


class MessageService:
    def __init__(self, repository: MessageRepository) -> None:
        self._repository = repository

    async def create(
        self,
        user_id: str,
        event_id: str,
        message_type: str,
        title: str,
        summary: str,
        related_type: str | None,
        related_id: str | None,
        now: datetime,
    ) -> InboxMessage:
        return await self._repository.create_once(
            user_id,
            event_id,
            message_type,
            title,
            summary,
            related_type,
            related_id,
            now,
        )

    async def list_messages(
        self, user_id: str, *, cursor: str | None = None, limit: int = 50
    ) -> MessagePage:
        values = await self._repository.list_messages(user_id, after_id=cursor, limit=limit + 1)
        visible = values[:limit]
        return MessagePage(
            visible,
            visible[-1].id if len(values) > limit else None,
            len(values) > limit,
        )

    async def mark_read(self, user_id: str, message_id: str, now: datetime) -> InboxMessage:
        return await self._repository.mark_read(user_id, message_id, now)

    async def unread_count(self, user_id: str) -> int:
        return await self._repository.count_unread(user_id)
