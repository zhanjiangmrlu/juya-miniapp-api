from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import cast

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from juya_miniapp_api.infrastructure.analytics_events import append_event
from juya_miniapp_api.modules.favorites.snapshots_sql import (
    dbtime,
    json_value,
    lock_active_user,
    utc,
)
from juya_miniapp_api.modules.short_reading.domain import (
    ReadingCompletion,
    ReadingPosition,
    ReadingProgress,
)
from juya_miniapp_api.modules.short_reading.repository import (
    completion_digest,
    completion_key,
    next_progress,
    preserved_position,
)
from juya_miniapp_api.shared.errors import AppError

ReadingGuard = Callable[[AsyncSession, str, str, int | None], Awaitable[None]]
_ENDPOINT = "v15:reading:complete"


class SQLAlchemyReadingRepository:
    """User lock serializes progress, final fact guard and command dedup in one transaction."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], guard: ReadingGuard) -> None:
        self.sessions = sessions
        self.guard = guard

    async def get(self, user_id: str, content_id: str) -> ReadingProgress | None:
        async with self.sessions() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT p.* FROM short_reading_progress p JOIN user_account "
                            "u ON u.id=p.user_id WHERE u.public_id=:user "
                            "AND p.short_reading_id=:content AND u.status='ACTIVE'"
                        ),
                        {"user": user_id, "content": content_id},
                    )
                )
                .mappings()
                .first()
            )
            return self._model(row) if row is not None else None

    async def open(
        self,
        user_id: str,
        content_id: str,
        version: int,
        paragraphs: dict[str, int],
        now: datetime,
    ) -> ReadingProgress:
        async with self.sessions() as session, session.begin():
            internal_user = await lock_active_user(session, user_id)
            await self.guard(session, user_id, content_id, version)
            old = await self._locked_progress(session, internal_user, content_id)
            position = preserved_position(old, version, paragraphs)
            row = next_progress(content_id, old, position, now)
            await self._write(session, internal_user, row)
            if old is None:
                await append_event(
                    session,
                    event_key=f"reading:start:{user_id}:{content_id}",
                    event_type="SHORT_READING_STARTED",
                    user_id=internal_user,
                    occurred_at=now,
                )
            return row

    async def save(
        self,
        user_id: str,
        content_id: str,
        position: ReadingPosition,
        now: datetime,
    ) -> ReadingProgress:
        async with self.sessions() as session, session.begin():
            internal_user = await lock_active_user(session, user_id)
            await self.guard(session, user_id, content_id, position.content_version)
            old = await self._locked_progress(session, internal_user, content_id)
            if (
                old
                and old.saved_content_version == position.content_version
                and (position.client_sequence <= old.client_sequence)
            ):
                return old
            row = next_progress(content_id, old, position, now)
            await self._write(session, internal_user, row)
            return row

    async def replay_completion(
        self,
        user_id: str,
        content_id: str,
        key: str,
        version: int,
    ) -> ReadingCompletion | None:
        async with self.sessions() as session, session.begin():
            internal_user = await lock_active_user(session, user_id)
            await self.guard(session, user_id, content_id, None)
            return await self._replay(session, internal_user, content_id, key, version)

    async def complete(
        self,
        user_id: str,
        content_id: str,
        key: str,
        version: int,
        paragraphs: dict[str, int],
        now: datetime,
    ) -> ReadingCompletion:
        async with self.sessions() as session, session.begin():
            internal_user = await lock_active_user(session, user_id)
            # Even a saved receipt requires current access while these locks are held.
            await self.guard(session, user_id, content_id, None)
            replay = await self._replay(session, internal_user, content_id, key, version)
            if replay:
                return replay
            await self.guard(session, user_id, content_id, version)
            old = await self._locked_progress(session, internal_user, content_id)
            created = old is None or old.completed_at is None
            position = preserved_position(old, version, paragraphs)
            row = next_progress(content_id, old, position, now, complete=True)
            await self._write(session, internal_user, row)
            result = ReadingCompletion(**row.model_dump(), created=created)
            if created:
                await append_event(
                    session,
                    event_key=f"reading:complete:{user_id}:{content_id}",
                    event_type="SHORT_READING_COMPLETED",
                    user_id=internal_user,
                    occurred_at=now,
                )
            await session.execute(
                text(
                    "INSERT INTO user_command_dedup "
                    "(user_id,endpoint,idempotency_key,request_hash,response_snapshot,expires_at) "
                    "VALUES(:user,:endpoint,:key,:hash,:response,:expires)"
                ),
                {
                    "user": internal_user,
                    "endpoint": _ENDPOINT,
                    "key": completion_key(content_id, key),
                    "hash": bytes.fromhex(completion_digest(content_id, version)),
                    "response": result.model_dump_json(),
                    # Match snapshot command receipt lifetime; account cleanup owns deletion.
                    "expires": dbtime(now + timedelta(days=36500)),
                },
            )
            return result

    @staticmethod
    async def _locked_progress(
        session: AsyncSession,
        user_id: int,
        content_id: str,
    ) -> ReadingProgress | None:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM short_reading_progress WHERE user_id=:user "
                        "AND short_reading_id=:content FOR UPDATE"
                    ),
                    {"user": user_id, "content": content_id},
                )
            )
            .mappings()
            .first()
        )
        return SQLAlchemyReadingRepository._model(row) if row is not None else None

    @staticmethod
    async def _replay(
        session: AsyncSession,
        user_id: int,
        content_id: str,
        key: str,
        version: int,
    ) -> ReadingCompletion | None:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT request_hash,response_snapshot FROM user_command_dedup "
                        "WHERE user_id=:user AND endpoint=:endpoint AND idempotency_key=:key"
                    ),
                    {
                        "user": user_id,
                        "endpoint": _ENDPOINT,
                        "key": completion_key(content_id, key),
                    },
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        if bytes(row["request_hash"]).hex() != completion_digest(content_id, version):
            raise AppError("IDEMPOTENCY_CONFLICT", "幂等键已用于其他完成请求", 409)
        return ReadingCompletion.model_validate(json_value(row["response_snapshot"]))

    @staticmethod
    async def _write(session: AsyncSession, user_id: int, row: ReadingProgress) -> None:
        position = ReadingPosition(
            content_version=row.saved_content_version,
            paragraph_id=row.paragraph_id,
            offset=row.offset,
            client_sequence=row.client_sequence,
        )
        await session.execute(
            text(
                "INSERT INTO short_reading_progress "
                "(user_id,short_reading_id,saved_content_version,paragraph_id,offset,position,"
                "last_client_sequence,started_at,last_read_at,completed_at) "
                "VALUES(:user,:content,:version,:paragraph,:offset,:position,:sequence,"
                ":started,:now,:completed) ON DUPLICATE KEY UPDATE "
                "saved_content_version=VALUES(saved_content_version),paragraph_id=VALUES(paragraph_id),"
                "offset=VALUES(offset),position=VALUES(position),"
                "last_client_sequence=VALUES(last_client_sequence),"
                "last_read_at=GREATEST(last_read_at,VALUES(last_read_at)),"
                "completed_at=COALESCE(completed_at,VALUES(completed_at))"
            ),
            {
                "user": user_id,
                "content": row.content_id,
                "version": row.saved_content_version,
                "paragraph": row.paragraph_id,
                "offset": row.offset,
                "position": position.model_dump_json(),
                "sequence": row.client_sequence,
                "started": dbtime(row.started_at),
                "now": dbtime(row.last_read_at),
                "completed": dbtime(row.completed_at) if row.completed_at else None,
            },
        )

    @staticmethod
    def _model(row: RowMapping) -> ReadingProgress:
        return ReadingProgress(
            content_id=row["short_reading_id"],
            saved_content_version=row["saved_content_version"],
            paragraph_id=row["paragraph_id"],
            offset=row["offset"],
            client_sequence=row["last_client_sequence"],
            started_at=cast(datetime, utc(row["started_at"])),
            last_read_at=cast(datetime, utc(row["last_read_at"])),
            completed_at=utc(row["completed_at"]),
        )
