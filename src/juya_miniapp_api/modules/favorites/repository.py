import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from sqlalchemy import bindparam, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from juya_miniapp_api.infrastructure.analytics_events import append_activity_events, append_event
from juya_miniapp_api.modules.checkins.service import beijing_learning_date
from juya_miniapp_api.modules.favorites.domain import (
    FavoriteEntry,
    FavoriteSource,
    ReviewCompletion,
    ReviewSession,
)
from juya_miniapp_api.shared.errors import AppError
from juya_miniapp_api.shared.ids import new_ulid


class FavoriteRepository(Protocol):
    async def upsert(
        self,
        user_id: str,
        entry_type: str,
        normalized_key: str,
        entry_stable_id: str,
        source: FavoriteSource,
        now: datetime,
    ) -> FavoriteEntry: ...

    async def get(self, user_id: str, favorite_id: str) -> FavoriteEntry | None: ...

    async def delete(self, user_id: str, favorite_id: str) -> None: ...

    async def create_review(
        self,
        user_id: str,
        card_ids: tuple[str, ...],
        idempotency_key: str,
        now: datetime,
    ) -> ReviewSession: ...

    async def complete_review(
        self,
        user_id: str,
        review_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> ReviewCompletion: ...


class InMemoryFavoriteRepository:
    def __init__(self) -> None:
        self.favorites: dict[tuple[str, str, str], FavoriteEntry] = {}
        self.sources: dict[str, dict[tuple[str, str], FavoriteSource]] = {}
        self.reviews: dict[str, ReviewSession] = {}
        self.review_idempotency: dict[tuple[str, str], str] = {}
        self.checkins: set[tuple[str, object]] = set()
        self._lock = asyncio.Lock()

    async def upsert(
        self,
        user_id: str,
        entry_type: str,
        normalized_key: str,
        entry_stable_id: str,
        source: FavoriteSource,
        now: datetime,
    ) -> FavoriteEntry:
        async with self._lock:
            key = (user_id, entry_type, normalized_key)
            current = self.favorites.get(key)
            if current is None:
                current = FavoriteEntry(
                    new_ulid(now),
                    user_id,
                    entry_type,
                    normalized_key,
                    entry_stable_id,
                    now,
                    None,
                )
            source_map = self.sources.setdefault(current.public_id, {})
            source_map.setdefault(
                (source.scene_id, (source.revision_id or "") + ":" + source.source_locator), source
            )
            updated = FavoriteEntry(
                current.public_id,
                current.user_id,
                current.entry_type,
                current.normalized_key,
                current.entry_stable_id,
                current.favorited_at,
                current.last_reviewed_at,
                tuple(source_map.values()),
            )
            self.favorites[key] = updated
            return updated

    async def get(self, user_id: str, favorite_id: str) -> FavoriteEntry | None:
        return next(
            (
                item
                for item in self.favorites.values()
                if item.user_id == user_id and item.public_id == favorite_id
            ),
            None,
        )

    async def delete(self, user_id: str, favorite_id: str) -> None:
        async with self._lock:
            keys = [
                key
                for key, item in self.favorites.items()
                if item.user_id == user_id and item.public_id == favorite_id
            ]
            for key in keys:
                del self.favorites[key]
            self.sources.pop(favorite_id, None)

    async def create_review(
        self,
        user_id: str,
        card_ids: tuple[str, ...],
        idempotency_key: str,
        now: datetime,
    ) -> ReviewSession:
        async with self._lock:
            existing_id = self.review_idempotency.get((user_id, idempotency_key))
            if existing_id is not None:
                existing = self.reviews[existing_id]
                if existing.card_ids != card_ids:
                    raise AppError("IDEMPOTENCY_KEY_CONFLICT", "幂等键已用于不同卡片集合", 409)
                return existing
            if not card_ids or len(set(card_ids)) != len(card_ids):
                raise AppError("REVIEW_CARDS_INVALID", "复习卡片无效", 422)
            for card in card_ids:
                if await self.get(user_id, card) is None:
                    raise AppError("REVIEW_CARDS_INVALID", "复习卡片不存在或不属于本人", 422)
            review = ReviewSession(
                new_ulid(now),
                user_id,
                "FAVORITES",
                now,
                None,
                len(card_ids),
                idempotency_key,
                card_ids,
            )
            self.reviews[review.id] = review
            self.review_idempotency[(user_id, idempotency_key)] = review.id
            return review

    async def complete_review(
        self,
        user_id: str,
        review_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> ReviewCompletion:
        del idempotency_key
        async with self._lock:
            review = self.reviews.get(review_id)
            if review is None or review.user_id != user_id:
                raise AppError("REVIEW_NOT_FOUND", "复习不存在", 404)
            created = review.completed_at is None
            if created:
                if not review.card_ids or len(review.card_ids) != review.card_count:
                    raise AppError("REVIEW_CARDS_INVALID", "复习卡片集合无效", 409)
                cards = [await self.get(user_id, card) for card in review.card_ids]
                if any(card is None for card in cards):
                    raise AppError("REVIEW_CARDS_INVALID", "复习卡片已删除", 409)
                for key, favorite in self.favorites.items():
                    if favorite.user_id == user_id and favorite.public_id in review.card_ids:
                        self.favorites[key] = replace(
                            favorite, last_reviewed_at=max(favorite.last_reviewed_at or now, now)
                        )
                review.completed_at = now
                self.checkins.add((user_id, beijing_learning_date(now)))
            return ReviewCompletion(
                review, created, beijing_learning_date(review.completed_at or now)
            )


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


class SQLAlchemyFavoriteRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def upsert(
        self,
        user_id: str,
        entry_type: str,
        normalized_key: str,
        entry_stable_id: str,
        source: FavoriteSource,
        now: datetime,
    ) -> FavoriteEntry:
        async with self._session_factory() as session, session.begin():
            internal_user_id = await self._lock_user(session, user_id)
            favorite_id = await session.scalar(
                text(
                    "SELECT id FROM favorite_entry WHERE user_id = :user_id "
                    "AND entry_type = :entry_type AND normalized_key = :normalized_key "
                    "FOR UPDATE"
                ),
                {
                    "user_id": internal_user_id,
                    "entry_type": entry_type,
                    "normalized_key": normalized_key,
                },
            )
            if favorite_id is None:
                public_id = new_ulid(now)
                await session.execute(
                    text(
                        "INSERT INTO favorite_entry "
                        "(public_id, user_id, entry_type, normalized_key, entry_stable_id, "
                        "favorited_at) VALUES (:public_id, :user_id, :entry_type, "
                        ":normalized_key, :entry_stable_id, :now)"
                    ),
                    {
                        "public_id": public_id,
                        "user_id": internal_user_id,
                        "entry_type": entry_type,
                        "normalized_key": normalized_key,
                        "entry_stable_id": entry_stable_id,
                        "now": _database_datetime(now),
                    },
                )
                favorite_id = await session.scalar(text("SELECT LAST_INSERT_ID()"))
                await append_event(
                    session,
                    event_key=f"favorite:{public_id}",
                    event_type="FAVORITE_CREATED",
                    user_id=internal_user_id,
                    occurred_at=now,
                    dimension=f"scene:{source.scene_id}",
                )
            if favorite_id is None:
                raise RuntimeError("Favorite insert did not return an id")
            await session.execute(
                text(
                    "INSERT INTO favorite_source "
                    "(favorite_id, scene_id, sentence_snapshot, source_locator, revision_id, "
                    "entry_version, entry_snapshot) "
                    "VALUES (:favorite_id, :scene_id, :snapshot, :locator, :revision, :version, "
                    ":entry_snapshot) ON DUPLICATE KEY UPDATE favorite_id = VALUES(favorite_id)"
                ),
                {
                    "favorite_id": favorite_id,
                    "scene_id": source.scene_id,
                    "snapshot": source.sentence_snapshot,
                    "locator": ((source.revision_id + "|") if source.revision_id else "")
                    + source.source_locator,
                    "revision": source.revision_id,
                    "version": source.entry_version,
                    "entry_snapshot": json.dumps(source.entry_snapshot),
                },
            )
            favorite = await self._load_by_internal_id(session, int(favorite_id), user_id)
            if favorite is None:
                raise RuntimeError("Favorite upsert was not visible")
            return favorite

    async def get(self, user_id: str, favorite_id: str) -> FavoriteEntry | None:
        async with self._session_factory() as session:
            internal_id = await session.scalar(
                text(
                    "SELECT f.id FROM favorite_entry f "
                    "JOIN user_account u ON u.id = f.user_id "
                    "WHERE u.public_id = :user_id AND f.public_id = :favorite_id"
                ),
                {"user_id": user_id, "favorite_id": favorite_id},
            )
            if internal_id is None:
                return None
            return await self._load_by_internal_id(session, int(internal_id), user_id)

    async def delete(self, user_id: str, favorite_id: str) -> None:
        async with self._session_factory() as session, session.begin():
            await self._lock_user(session, user_id)
            await session.execute(
                text(
                    "DELETE f FROM favorite_entry f "
                    "JOIN user_account u ON u.id = f.user_id "
                    "WHERE u.public_id = :user_id AND f.public_id = :favorite_id"
                ),
                {"user_id": user_id, "favorite_id": favorite_id},
            )

    async def create_review(
        self,
        user_id: str,
        card_ids: tuple[str, ...],
        idempotency_key: str,
        now: datetime,
    ) -> ReviewSession:
        async with self._session_factory() as session, session.begin():
            internal_user_id = await self._lock_user(session, user_id)
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT id, review_type, started_at, completed_at, card_count, "
                            "idempotency_key, card_ids FROM review_session WHERE user_id = "
                            ":user_id "
                            "AND idempotency_key = :idempotency_key FOR UPDATE"
                        ),
                        {"user_id": internal_user_id, "idempotency_key": idempotency_key},
                    )
                )
                .mappings()
                .first()
            )
            if row is not None:
                review = self._review_from_row(row, user_id)
                if review.card_ids != card_ids:
                    raise AppError("IDEMPOTENCY_KEY_CONFLICT", "幂等键已用于不同卡片集合", 409)
                return review
            await self._validate_cards(session, internal_user_id, card_ids)
            review_id = new_ulid(now)
            await session.execute(
                text(
                    "INSERT INTO review_session "
                    "(id, user_id, review_type, started_at, card_count, idempotency_key, card_ids) "
                    "VALUES (:id, :user_id, 'FAVORITES', :now, :card_count, :idempotency_key, "
                    ":cards)"
                ),
                {
                    "id": review_id,
                    "user_id": internal_user_id,
                    "now": _database_datetime(now),
                    "card_count": len(card_ids),
                    "cards": json.dumps(card_ids),
                    "idempotency_key": idempotency_key,
                },
            )
            return ReviewSession(
                review_id, user_id, "FAVORITES", now, None, len(card_ids), idempotency_key, card_ids
            )

    async def complete_review(
        self,
        user_id: str,
        review_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> ReviewCompletion:
        del idempotency_key
        learning_day = beijing_learning_date(now)
        async with self._session_factory() as session, session.begin():
            internal_user_id = await self._lock_user(session, user_id)
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT id, review_type, started_at, completed_at, card_count, "
                            "idempotency_key, card_ids FROM review_session WHERE id = :review_id "
                            "AND user_id = :user_id FOR UPDATE"
                        ),
                        {"review_id": review_id, "user_id": internal_user_id},
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise AppError("REVIEW_NOT_FOUND", "复习不存在", 404)
            created = row["completed_at"] is None
            if created:
                review = self._review_from_row(row, user_id)
                if len(review.card_ids) != review.card_count:
                    raise AppError("REVIEW_CARDS_INVALID", "复习卡片集合无效", 409)
                await self._validate_cards(session, internal_user_id, review.card_ids)
                await session.execute(
                    text(
                        "UPDATE favorite_entry SET "
                        "last_reviewed_at=GREATEST(COALESCE(last_reviewed_at,:now),:now) WHERE "
                        "user_id=:user "
                        "AND public_id IN :cards"
                    ).bindparams(bindparam("cards", expanding=True)),
                    {
                        "now": _database_datetime(now),
                        "user": internal_user_id,
                        "cards": review.card_ids,
                    },
                )
                await append_event(
                    session,
                    event_key=f"review:{review_id}",
                    event_type="REVIEW_COMPLETED",
                    user_id=internal_user_id,
                    occurred_at=now,
                )
                await append_activity_events(session, internal_user_id, now)
                await session.execute(
                    text("UPDATE review_session SET completed_at = :now WHERE id = :review_id"),
                    {"now": _database_datetime(now), "review_id": review_id},
                )
                await session.execute(
                    text(
                        "INSERT IGNORE INTO daily_checkin "
                        "(user_id, beijing_date, completion_source) "
                        "VALUES (:user_id, :learning_day, 'REVIEW')"
                    ),
                    {"user_id": internal_user_id, "learning_day": learning_day},
                )
            review = self._review_from_row(row, user_id)
            if created:
                review.completed_at = now
            return ReviewCompletion(
                review, created, beijing_learning_date(review.completed_at or now)
            )

    async def list_favorites(
        self, user_id: str, *, after_id: str | None = None, limit: int = 50
    ) -> list[FavoriteEntry]:
        cursor = "" if after_id is None else after_id
        async with self._session_factory() as session:
            rows = cast(
                list[int],
                (
                    await session.execute(
                        text(
                            "SELECT f.id FROM favorite_entry f "
                            "JOIN user_account u ON u.id = f.user_id "
                            "WHERE u.public_id = :user_id AND f.public_id > :cursor "
                            "ORDER BY f.public_id LIMIT :limit"
                        ),
                        {"user_id": user_id, "cursor": cursor, "limit": limit},
                    )
                )
                .scalars()
                .all(),
            )
            favorites: list[FavoriteEntry] = []
            for internal_id in rows:
                favorite = await self._load_by_internal_id(session, int(internal_id), user_id)
                if favorite is not None:
                    favorites.append(favorite)
            return favorites

    @staticmethod
    async def _validate_cards(session: AsyncSession, user_id: int, cards: tuple[str, ...]) -> None:
        if not cards or len(set(cards)) != len(cards):
            raise AppError("REVIEW_CARDS_INVALID", "复习卡片集合无效", 422)
        rows: Any = (
            (
                await session.execute(
                    text(
                        "SELECT public_id FROM favorite_entry WHERE user_id=:user AND "
                        "public_id IN :cards FOR UPDATE"
                    ).bindparams(bindparam("cards", expanding=True)),
                    {"user": user_id, "cards": cards},
                )
            )
            .scalars()
            .all()
        )
        if set(rows) != set(cards):
            raise AppError("REVIEW_CARDS_INVALID", "复习卡片不存在或不属于本人", 422)

    @staticmethod
    async def _lock_user(session: AsyncSession, public_id: str) -> int:
        internal_id = await session.scalar(
            text("SELECT id FROM user_account WHERE public_id = :public_id FOR UPDATE"),
            {"public_id": public_id},
        )
        if internal_id is None:
            raise AppError("USER_NOT_FOUND", "用户不存在", 404)
        return int(internal_id)

    @staticmethod
    async def _load_by_internal_id(
        session: AsyncSession, internal_id: int, public_user_id: str
    ) -> FavoriteEntry | None:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT public_id, entry_type, normalized_key, entry_stable_id, "
                        "favorited_at, last_reviewed_at FROM favorite_entry WHERE id = :id"
                    ),
                    {"id": internal_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        source_rows = (
            (
                await session.execute(
                    text(
                        "SELECT scene_id, sentence_snapshot, source_locator, revision_id, "
                        "entry_version, entry_snapshot "
                        "FROM favorite_source WHERE favorite_id = :favorite_id ORDER BY id"
                    ),
                    {"favorite_id": internal_id},
                )
            )
            .mappings()
            .all()
        )
        favorited_at = _utc_datetime(row["favorited_at"])
        if favorited_at is None:
            raise RuntimeError("Favorite timestamp cannot be null")
        return FavoriteEntry(
            row["public_id"],
            public_user_id,
            row["entry_type"],
            row["normalized_key"],
            row["entry_stable_id"],
            favorited_at,
            _utc_datetime(row["last_reviewed_at"]),
            tuple(
                FavoriteSource(
                    item["scene_id"],
                    item["sentence_snapshot"],
                    item["source_locator"].split("|", 1)[-1],
                    None,
                    item["revision_id"],
                    item["entry_version"],
                    json.loads(item["entry_snapshot"])
                    if isinstance(item["entry_snapshot"], str)
                    else (item["entry_snapshot"] or {}),
                )
                for item in source_rows
            ),
        )

    @staticmethod
    def _review_from_row(row: RowMapping, user_id: str) -> ReviewSession:
        started_at = _utc_datetime(row["started_at"])
        if started_at is None:
            raise RuntimeError("Review timestamp cannot be null")
        return ReviewSession(
            row["id"],
            user_id,
            row["review_type"],
            started_at,
            _utc_datetime(row["completed_at"]),
            row["card_count"],
            row["idempotency_key"],
            tuple(
                json.loads(row["card_ids"])
                if isinstance(row["card_ids"], str)
                else row["card_ids"] or ()
            ),
        )
