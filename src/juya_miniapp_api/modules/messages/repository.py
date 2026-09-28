from datetime import UTC, datetime
from typing import Protocol, cast

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from juya_miniapp_api.modules.messages.domain import InboxMessage
from juya_miniapp_api.shared.errors import AppError
from juya_miniapp_api.shared.ids import new_ulid


class MessageRepository(Protocol):
    async def create_once(
        self,
        user_id: str,
        event_id: str,
        message_type: str,
        title: str,
        summary: str,
        related_type: str | None,
        related_id: str | None,
        now: datetime,
    ) -> InboxMessage: ...

    async def list_messages(
        self, user_id: str, *, after_id: str | None, limit: int
    ) -> list[InboxMessage]: ...

    async def mark_read(self, user_id: str, message_id: str, now: datetime) -> InboxMessage: ...

    async def count_unread(self, user_id: str) -> int: ...


class InMemoryMessageRepository:
    def __init__(self) -> None:
        self.messages: dict[str, InboxMessage] = {}
        self.event_ids: dict[str, str] = {}

    async def create_once(
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
        existing_id = self.event_ids.get(event_id)
        if existing_id is not None:
            existing = self.messages[existing_id]
            if existing.user_id != user_id:
                raise AppError("MESSAGE_EVENT_CONFLICT", "消息事件已被使用", 409)
            return existing
        item = InboxMessage(
            new_ulid(now),
            user_id,
            message_type,
            title,
            summary,
            event_id,
            now,
            related_type,
            related_id,
        )
        self.messages[item.id] = item
        self.event_ids[event_id] = item.id
        return item

    async def list_messages(
        self, user_id: str, *, after_id: str | None, limit: int
    ) -> list[InboxMessage]:
        values = sorted(
            (item for item in self.messages.values() if item.user_id == user_id),
            key=lambda item: item.id,
            reverse=True,
        )
        if after_id is not None:
            values = [item for item in values if item.id < after_id]
        return values[:limit]

    async def mark_read(self, user_id: str, message_id: str, now: datetime) -> InboxMessage:
        item = self.messages.get(message_id)
        if item is None or item.user_id != user_id:
            raise AppError("MESSAGE_NOT_FOUND", "消息不存在", 404)
        if item.read_at is None:
            item = InboxMessage(
                item.id,
                item.user_id,
                item.message_type,
                item.title,
                item.summary,
                item.event_id,
                item.created_at,
                item.related_type,
                item.related_id,
                now,
            )
            self.messages[item.id] = item
        return item

    async def count_unread(self, user_id: str) -> int:
        return sum(
            item.user_id == user_id and item.read_at is None for item in self.messages.values()
        )


class SQLAlchemyMessageRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def create_once(
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
        message_id = new_ulid(now)
        async with self._sessions() as session, session.begin():
            internal_user_id = await self._user_id(session, user_id)
            await session.execute(
                text(
                    "INSERT IGNORE INTO inbox_message "
                    "(id, user_id, message_type, related_type, related_id, title, summary, "
                    "event_id, created_at) VALUES (:id, :user_id, :message_type, :related_type, "
                    ":related_id, :title, :summary, :event_id, :created_at)"
                ),
                {
                    "id": message_id,
                    "user_id": internal_user_id,
                    "message_type": message_type,
                    "related_type": related_type,
                    "related_id": related_id,
                    "title": title,
                    "summary": summary,
                    "event_id": event_id,
                    "created_at": _database_datetime(now),
                },
            )
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT m.id, u.public_id AS user_id, m.message_type, m.title, "
                            "m.summary, m.event_id, m.created_at, m.related_type, m.related_id, "
                            "m.read_at FROM inbox_message m JOIN user_account u ON u.id=m.user_id "
                            "WHERE m.event_id=:event_id"
                        ),
                        {"event_id": event_id},
                    )
                )
                .mappings()
                .one()
            )
            if str(row["user_id"]) != user_id:
                raise AppError("MESSAGE_EVENT_CONFLICT", "消息事件已被使用", 409)
            return _message(row)

    async def list_messages(
        self, user_id: str, *, after_id: str | None, limit: int
    ) -> list[InboxMessage]:
        cursor_clause = " AND m.id < :after_id" if after_id is not None else ""
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT m.id, u.public_id AS user_id, m.message_type, m.title, "
                            "m.summary, m.event_id, m.created_at, m.related_type, m.related_id, "
                            "m.read_at FROM inbox_message m JOIN user_account u ON u.id=m.user_id "
                            "WHERE u.public_id=:user_id"
                            + cursor_clause
                            + " ORDER BY m.id DESC LIMIT :limit"
                        ),
                        {"user_id": user_id, "after_id": after_id, "limit": limit},
                    )
                )
                .mappings()
                .all()
            )
        return [_message(row) for row in rows]

    async def mark_read(self, user_id: str, message_id: str, now: datetime) -> InboxMessage:
        async with self._sessions() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE inbox_message m JOIN user_account u ON u.id=m.user_id "
                    "SET m.read_at=COALESCE(m.read_at, :read_at) "
                    "WHERE m.id=:message_id AND u.public_id=:user_id"
                ),
                {
                    "read_at": _database_datetime(now),
                    "message_id": message_id,
                    "user_id": user_id,
                },
            )
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT m.id, u.public_id AS user_id, m.message_type, m.title, "
                            "m.summary, m.event_id, m.created_at, m.related_type, m.related_id, "
                            "m.read_at FROM inbox_message m JOIN user_account u ON u.id=m.user_id "
                            "WHERE m.id=:message_id AND u.public_id=:user_id"
                        ),
                        {"message_id": message_id, "user_id": user_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise AppError("MESSAGE_NOT_FOUND", "消息不存在", 404)
            return _message(row)

    async def count_unread(self, user_id: str) -> int:
        async with self._sessions() as session:
            value = await session.scalar(
                text(
                    "SELECT COUNT(*) FROM inbox_message m JOIN user_account u ON u.id=m.user_id "
                    "WHERE u.public_id=:user_id AND m.read_at IS NULL"
                ),
                {"user_id": user_id},
            )
        return int(value or 0)

    @staticmethod
    async def _user_id(session: AsyncSession, public_id: str) -> int:
        value = await session.scalar(
            text("SELECT id FROM user_account WHERE public_id=:public_id"),
            {"public_id": public_id},
        )
        if value is None:
            raise AppError("USER_NOT_FOUND", "用户不存在", 404)
        return int(value)


def _database_datetime(value: datetime) -> datetime:
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized.astimezone(UTC).replace(tzinfo=None)


def _utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _message(row: RowMapping) -> InboxMessage:
    return InboxMessage(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        message_type=str(row["message_type"]),
        title=str(row["title"]),
        summary=str(row["summary"]),
        event_id=str(row["event_id"]),
        created_at=_utc_datetime(cast(datetime, row["created_at"])) or datetime.now(UTC),
        related_type=str(row["related_type"]) if row["related_type"] is not None else None,
        related_id=str(row["related_id"]) if row["related_id"] is not None else None,
        read_at=_utc_datetime(cast(datetime | None, row["read_at"])),
    )
