import asyncio
import json
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from juya_miniapp_api.modules.checkins.service import beijing_learning_date
from juya_miniapp_api.modules.learning.domain import (
    CompletionResult,
    LearningProgress,
    ReadingPosition,
)
from juya_miniapp_api.shared.errors import AppError


class LearningRepository(Protocol):
    async def save_progress(
        self,
        user_id: str,
        scene_id: str,
        client_sequence: int,
        position: ReadingPosition,
        now: datetime,
    ) -> LearningProgress: ...

    async def complete(
        self,
        user_id: str,
        scene_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> CompletionResult: ...


class InMemoryLearningRepository:
    def __init__(self) -> None:
        self.progress: dict[tuple[str, str], LearningProgress] = {}
        self.completion_events: dict[tuple[str, str], str] = {}
        self.idempotency_keys: dict[tuple[str, str], tuple[str, str]] = {}
        self.checkins: set[tuple[str, object]] = set()
        self._lock = asyncio.Lock()

    async def save_progress(
        self,
        user_id: str,
        scene_id: str,
        client_sequence: int,
        position: ReadingPosition,
        now: datetime,
    ) -> LearningProgress:
        async with self._lock:
            key = (user_id, scene_id)
            current = self.progress.get(key)
            if current is not None and client_sequence <= current.client_sequence:
                return current
            updated = LearningProgress(
                user_id=user_id,
                scene_id=scene_id,
                source_type="SCENE",
                position=position,
                client_sequence=client_sequence,
                started_at=current.started_at if current else now,
                completed_at=current.completed_at if current else None,
                last_learned_at=now,
            )
            self.progress[key] = updated
            return updated

    async def complete(
        self,
        user_id: str,
        scene_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> CompletionResult:
        async with self._lock:
            key = (user_id, scene_id)
            current = self.progress.get(key)
            created = current is None or current.completed_at is None
            if current is None:
                current = LearningProgress(
                    user_id,
                    scene_id,
                    "SCENE",
                    ReadingPosition("start", 0),
                    0,
                    now,
                    now,
                    now,
                )
            elif created:
                current = LearningProgress(
                    current.user_id,
                    current.scene_id,
                    current.source_type,
                    current.position,
                    current.client_sequence,
                    current.started_at,
                    now,
                    now,
                )
            self.progress[key] = current
            if created:
                self.completion_events[key] = idempotency_key
                self.idempotency_keys[(user_id, idempotency_key)] = key
            learning_day = beijing_learning_date(now)
            self.checkins.add((user_id, learning_day))
            return CompletionResult(current, created, learning_day)


def _database_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SQLAlchemyLearningRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save_progress(
        self,
        user_id: str,
        scene_id: str,
        client_sequence: int,
        position: ReadingPosition,
        now: datetime,
    ) -> LearningProgress:
        async with self._session_factory() as session, session.begin():
            internal_id = await self._lock_user(session, user_id)
            current = await self._load_progress(session, user_id, scene_id, for_update=True)
            serialized_position = json.dumps(
                {"entry_id": position.entry_id, "offset": position.offset},
                separators=(",", ":"),
            )
            if current is None:
                await session.execute(
                    text(
                        "INSERT INTO learning_progress "
                        "(user_id, scene_id, source_type, position, last_client_sequence, "
                        "started_at, last_learned_at) VALUES "
                        "(:user_id, :scene_id, 'SCENE', :position, :sequence, :now, :now)"
                    ),
                    {
                        "user_id": internal_id,
                        "scene_id": scene_id,
                        "position": serialized_position,
                        "sequence": client_sequence,
                        "now": _database_datetime(now),
                    },
                )
            elif client_sequence > current.client_sequence:
                await session.execute(
                    text(
                        "UPDATE learning_progress SET position = :position, "
                        "last_client_sequence = :sequence, last_learned_at = :now "
                        "WHERE user_id = :user_id AND scene_id = :scene_id"
                    ),
                    {
                        "position": serialized_position,
                        "sequence": client_sequence,
                        "now": _database_datetime(now),
                        "user_id": internal_id,
                        "scene_id": scene_id,
                    },
                )
            updated = await self._load_progress(session, user_id, scene_id)
            if updated is None:
                raise RuntimeError("Learning progress was not visible")
            return updated

    async def complete(
        self,
        user_id: str,
        scene_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> CompletionResult:
        learning_day = beijing_learning_date(now)
        async with self._session_factory() as session, session.begin():
            internal_id = await self._lock_user(session, user_id)
            current = await self._load_progress(session, user_id, scene_id, for_update=True)
            created = current is None or current.completed_at is None
            if current is None:
                await session.execute(
                    text(
                        "INSERT INTO learning_progress "
                        "(user_id, scene_id, source_type, position, last_client_sequence, "
                        "started_at, completed_at, last_learned_at) VALUES "
                        "(:user_id, :scene_id, 'SCENE', :position, 0, :now, :now, :now)"
                    ),
                    {
                        "user_id": internal_id,
                        "scene_id": scene_id,
                        "position": '{"entry_id":"start","offset":0}',
                        "now": _database_datetime(now),
                    },
                )
            elif created:
                await session.execute(
                    text(
                        "UPDATE learning_progress SET completed_at = :now, "
                        "last_learned_at = :now WHERE user_id = :user_id AND scene_id = :scene_id"
                    ),
                    {
                        "now": _database_datetime(now),
                        "user_id": internal_id,
                        "scene_id": scene_id,
                    },
                )
            if created:
                await session.execute(
                    text(
                        "INSERT INTO learning_completion_event "
                        "(user_id, scene_id, completed_at, idempotency_key) "
                        "VALUES (:user_id, :scene_id, :now, :idempotency_key)"
                    ),
                    {
                        "user_id": internal_id,
                        "scene_id": scene_id,
                        "now": _database_datetime(now),
                        "idempotency_key": idempotency_key,
                    },
                )
                await session.execute(
                    text(
                        "INSERT IGNORE INTO daily_checkin "
                        "(user_id, beijing_date, completion_source) "
                        "VALUES (:user_id, :learning_day, 'SCENE')"
                    ),
                    {"user_id": internal_id, "learning_day": learning_day},
                )
            progress = await self._load_progress(session, user_id, scene_id)
            if progress is None:
                raise RuntimeError("Completed progress was not visible")
            return CompletionResult(progress, created, learning_day)

    async def get_progress(self, user_id: str, scene_id: str) -> LearningProgress | None:
        async with self._session_factory() as session:
            return await self._load_progress(session, user_id, scene_id)

    async def list_history(self, user_id: str, limit: int = 100) -> list[LearningProgress]:
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT u.public_id, p.scene_id, p.source_type, p.position, "
                            "p.last_client_sequence, p.started_at, p.completed_at, "
                            "p.last_learned_at FROM learning_progress p "
                            "JOIN user_account u ON u.id = p.user_id "
                            "WHERE u.public_id = :public_id "
                            "ORDER BY p.last_learned_at DESC LIMIT :limit"
                        ),
                        {"public_id": user_id, "limit": limit},
                    )
                )
                .mappings()
                .all()
            )
        return [self._from_row(row) for row in rows]

    @staticmethod
    async def _lock_user(session: AsyncSession, public_id: str) -> int:
        internal_id = await session.scalar(
            text("SELECT id FROM user_account WHERE public_id = :public_id FOR UPDATE"),
            {"public_id": public_id},
        )
        if internal_id is None:
            raise AppError("USER_NOT_FOUND", "用户不存在", 404)
        return int(internal_id)

    @classmethod
    async def _load_progress(
        cls,
        session: AsyncSession,
        public_id: str,
        scene_id: str,
        *,
        for_update: bool = False,
    ) -> LearningProgress | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = (
            (
                await session.execute(
                    text(
                        "SELECT u.public_id, p.scene_id, p.source_type, p.position, "
                        "p.last_client_sequence, p.started_at, p.completed_at, "
                        "p.last_learned_at FROM learning_progress p "
                        "JOIN user_account u ON u.id = p.user_id "
                        "WHERE u.public_id = :public_id AND p.scene_id = :scene_id" + suffix
                    ),
                    {"public_id": public_id, "scene_id": scene_id},
                )
            )
            .mappings()
            .first()
        )
        return cls._from_row(row) if row is not None else None

    @staticmethod
    def _from_row(row: RowMapping) -> LearningProgress:
        raw_position = row["position"]
        payload = json.loads(raw_position) if isinstance(raw_position, str) else raw_position
        started_at = _utc_datetime(row["started_at"])
        last_learned_at = _utc_datetime(row["last_learned_at"])
        if started_at is None or last_learned_at is None:
            raise RuntimeError("Learning progress timestamps cannot be null")
        return LearningProgress(
            user_id=row["public_id"],
            scene_id=row["scene_id"],
            source_type=row["source_type"],
            position=ReadingPosition(payload["entry_id"], int(payload.get("offset", 0))),
            client_sequence=row["last_client_sequence"],
            started_at=started_at,
            completed_at=_utc_datetime(row["completed_at"]),
            last_learned_at=last_learned_at,
        )
