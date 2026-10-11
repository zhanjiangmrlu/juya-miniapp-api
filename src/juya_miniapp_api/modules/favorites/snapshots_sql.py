"""SQL persistence for the admin-owned 0017 snapshot tables."""

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import bindparam, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from juya_miniapp_api.infrastructure.analytics_events import append_activity_events, append_event
from juya_miniapp_api.modules.checkins.service import beijing_learning_date
from juya_miniapp_api.modules.favorites.snapshots import (
    ConfirmedSnapshot,
    EntryType,
    FavoriteSnapshot,
    PronunciationReference,
    SnapshotReference,
    SnapshotReview,
    SnapshotService,
    request_digest,
)
from juya_miniapp_api.shared.errors import AppError
from juya_miniapp_api.shared.ids import new_ulid

SnapshotGuard = Callable[[AsyncSession, str, ConfirmedSnapshot], Awaitable[None]]

SELECT_SNAPSHOT = """
SELECT f.*, r.content_type, r.content_id, r.saved_content_version, r.revision_id,
       r.english, r.phonetic, r.meaning_zh, r.explanation, r.source_sentence,
       r.source_locator, r.safe_title_en, r.safe_title_zh, r.image_asset_id,
       r.pronunciation_target_id, r.resource_version,
       r.legacy_incomplete, r.missing_fields,
       t.allow_synthesis, t.status AS pronunciation_status
FROM favorite_snapshot f
JOIN favorite_snapshot_revision r ON r.public_id=f.current_snapshot_revision_id
JOIN user_account u ON u.id=f.user_id
LEFT JOIN pronunciation_target t ON t.public_id=r.pronunciation_target_id
"""


def dbtime(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def utc(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def json_value(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


async def lock_active_user(session: AsyncSession, public_id: str) -> int:
    row = (
        (
            await session.execute(
                text("SELECT id,status FROM user_account WHERE public_id=:user FOR UPDATE"),
                {"user": public_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None or row["status"] != "ACTIVE":
        raise AppError("ACCOUNT_NOT_ACTIVE", "账号当前不可用", 403)
    return int(row["id"])


class SQLAlchemySnapshotRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], guard: SnapshotGuard) -> None:
        self.sessions = sessions
        self.guard = guard

    async def replay(
        self, user_id: str, action: str, key: str, request_hash: str
    ) -> FavoriteSnapshot | None:
        async with self.sessions() as session:
            internal = await session.scalar(
                text("SELECT id FROM user_account WHERE public_id=:user AND status='ACTIVE'"),
                {"user": user_id},
            )
            if internal is None:
                raise AppError("ACCOUNT_NOT_ACTIVE", "账号当前不可用", 403)
            return await self._replay(session, int(internal), action, key, request_hash)

    @staticmethod
    async def _replay(
        session: AsyncSession, user_id: int, action: str, key: str, digest: str
    ) -> FavoriteSnapshot | None:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT request_hash,response_snapshot FROM user_command_dedup "
                        "WHERE user_id=:user AND endpoint=:endpoint AND idempotency_key=:key"
                    ),
                    {"user": user_id, "endpoint": "v15:snapshot:" + action, "key": key},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        if bytes(row["request_hash"]).hex() != digest:
            raise AppError("IDEMPOTENCY_CONFLICT", "幂等键已用于其他操作", 409)
        return FavoriteSnapshot.model_validate(json_value(row["response_snapshot"]))

    @staticmethod
    async def _remember(
        session: AsyncSession,
        user_id: int,
        action: str,
        key: str,
        digest: str,
        row: FavoriteSnapshot,
        now: datetime,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO user_command_dedup "
                "(user_id,endpoint,idempotency_key,request_hash,response_snapshot,expires_at) "
                "VALUES(:user,:endpoint,:key,:hash,:response,:expires)"
            ),
            {
                "user": user_id,
                "endpoint": "v15:snapshot:" + action,
                "key": key,
                "hash": bytes.fromhex(digest),
                "response": row.model_dump_json(),
                "expires": dbtime(now + timedelta(days=36500)),
            },
        )

    async def create(
        self,
        user_id: str,
        ref: SnapshotReference,
        key: str,
        now: datetime,
        confirm: Callable[[], Awaitable[ConfirmedSnapshot]],
    ) -> FavoriteSnapshot:
        digest = request_digest(ref.model_dump())
        replay = await self.replay(user_id, "create", key, digest)
        if replay is not None:
            return replay
        # No SQL lock is held across HTTP; final facts are checked under SQL locks below.
        data = await confirm()
        if (
            data.reference.model_dump()
            != ref.model_copy(
                update={"entry_type": ref.entry_type or data.reference.entry_type}
            ).model_dump()
        ):
            raise AppError("CONTENT_VERSION_CONFLICT", "内容已更新,请刷新后收藏", 409)
        ref = data.reference
        async with self.sessions() as session, session.begin():
            internal_user = await lock_active_user(session, user_id)
            replay = await self._replay(session, internal_user, "create", key, digest)
            if replay is not None:
                return replay
            await self.guard(session, user_id, data)
            identity = {
                "user": internal_user,
                "kind": ref.entry_type,
                "entry": ref.entry_id,
                "sense": ref.sense_id,
                "source": ref.source_id,
            }
            current = (
                (
                    await session.execute(
                        text(
                            "SELECT id,public_id,active,snapshot_version FROM favorite_snapshot "
                            "WHERE user_id=:user AND entry_type=:kind AND entry_id=:entry "
                            "AND sense_id=:sense AND source_id=:source FOR UPDATE"
                        ),
                        identity,
                    )
                )
                .mappings()
                .first()
            )
            if current is not None and current["active"]:
                row = await self._get(session, user_id, str(current["public_id"]))
                if row is None:
                    raise RuntimeError("Active snapshot revision missing")
            else:
                favorite_id = str(current["public_id"]) if current else new_ulid(now)
                version = int(current["snapshot_version"]) + 1 if current else 1
                revision_id = new_ulid(now)
                if current is None:
                    await session.execute(
                        text(
                            "INSERT INTO favorite_snapshot(public_id,user_id,entry_type,entry_id,"
                            "sense_id,source_id,active,snapshot_version,favorited_at) "
                            "VALUES(:fid,:user,:kind,:entry,:sense,:source,1,:version,:now)"
                        ),
                        identity | {"fid": favorite_id, "version": version, "now": dbtime(now)},
                    )
                    internal_favorite = int(await session.scalar(text("SELECT LAST_INSERT_ID()")))
                else:
                    internal_favorite = int(current["id"])
                pronunciation = data.pronunciation
                params = {
                    "rid": revision_id,
                    "fid": internal_favorite,
                    "version": version,
                    "content_type": ref.content_type,
                    "content_id": ref.content_id,
                    "content_version": ref.content_version,
                    "revision": data.revision_id,
                    "english": data.english,
                    "phonetic": data.phonetic,
                    "meaning": data.meaning_zh,
                    "explanation": data.explanation,
                    "sentence": data.source_sentence,
                    "locator": json.dumps(data.source_locator),
                    "title": data.source_title,
                    "image": data.image_asset_id,
                    "target": pronunciation.target_id if pronunciation else None,
                    "resource": pronunciation.resource_version if pronunciation else None,
                }
                await session.execute(
                    text(
                        "INSERT INTO "
                        "favorite_snapshot_revision(public_id,favorite_id,snapshot_version,"
                        "content_type,content_id,saved_content_version,revision_id,"
                        "english,phonetic,"
                        "meaning_zh,explanation,source_sentence,source_locator,safe_title_zh,"
                        "image_asset_id,pronunciation_target_id,resource_version) VALUES("
                        ":rid,:fid,:version,:content_type,:content_id,:content_version,:revision,"
                        ":english,:phonetic,:meaning,:explanation,:sentence,:locator,:title,"
                        ":image,:target,:resource)"
                    ),
                    params,
                )
                await session.execute(
                    text(
                        "UPDATE favorite_snapshot SET active=1,current_snapshot_revision_id=:rid,"
                        "snapshot_version=:version,favorited_at=:now,"
                        "cancelled_at=NULL WHERE id=:fid"
                    ),
                    {
                        "rid": revision_id,
                        "version": version,
                        "now": dbtime(now),
                        "fid": internal_favorite,
                    },
                )
                row = FavoriteSnapshot(
                    favorite_id=favorite_id,
                    active=True,
                    snapshot_version=version,
                    snapshot_revision_id=revision_id,
                    saved_content_version=ref.content_version,
                    favorited_at=now,
                    data=data,
                )
                await self._reference(session, row, revision_id, now)
                await append_event(
                    session,
                    event_key="snapshot:" + revision_id,
                    event_type="FAVORITE_CREATED",
                    user_id=internal_user,
                    occurred_at=now,
                )
            await self._remember(session, internal_user, "create", key, digest, row, now)
            return row

    async def cancel(
        self, user_id: str, favorite_id: str, key: str, now: datetime
    ) -> FavoriteSnapshot:
        digest = request_digest(favorite_id)
        async with self.sessions() as session, session.begin():
            internal_user = await lock_active_user(session, user_id)
            replay = await self._replay(session, internal_user, "cancel", key, digest)
            if replay is not None:
                return replay
            row = await self._get(session, user_id, favorite_id)
            if row is None:
                raise AppError("FAVORITE_NOT_FOUND", "收藏不存在", 404)
            if row.active:
                await session.execute(
                    text(
                        "UPDATE favorite_snapshot SET active=0,cancelled_at=:now "
                        "WHERE public_id=:fid AND user_id=:user"
                    ),
                    {"now": dbtime(now), "fid": favorite_id, "user": internal_user},
                )
                row = row.model_copy(update={"active": False})
                revision = await session.scalar(
                    text(
                        "SELECT current_snapshot_revision_id FROM favorite_snapshot "
                        "WHERE public_id=:fid"
                    ),
                    {"fid": favorite_id},
                )
                await self._reference(session, row, str(revision), now)
            await self._remember(session, internal_user, "cancel", key, digest, row, now)
            return row

    async def get(self, user_id: str, favorite_id: str) -> FavoriteSnapshot | None:
        async with self.sessions() as session:
            return await self._get(session, user_id, favorite_id)

    async def _get(
        self, session: AsyncSession, user_id: str, favorite_id: str
    ) -> FavoriteSnapshot | None:
        row = (
            (
                await session.execute(
                    text(
                        SELECT_SNAPSHOT
                        + " WHERE u.public_id=:user AND f.public_id=:fid AND u.status='ACTIVE'"
                    ),
                    {"user": user_id, "fid": favorite_id},
                )
            )
            .mappings()
            .first()
        )
        return self._model(row) if row is not None else None

    async def list_active(
        self, user_id: str, entry_type: EntryType | None, after: str | None, limit: int
    ) -> list[FavoriteSnapshot]:
        async with self.sessions() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            SELECT_SNAPSHOT
                            + " WHERE u.public_id=:user AND u.status='ACTIVE' AND f.active=1 "
                            "AND f.public_id>:after AND (:kind IS NULL OR f.entry_type=:kind "
                            "OR f.entry_type=:legacy_kind) "
                            "ORDER BY f.public_id LIMIT :limit"
                        ),
                        {
                            "user": user_id,
                            "after": after or "",
                            "kind": entry_type,
                            "limit": limit,
                            "legacy_kind": {"word": "VOCABULARY", "chunk": "PHRASE"}.get(
                                entry_type or ""
                            ),
                        },
                    )
                )
                .mappings()
                .all()
            )
            return [self._model(row) for row in rows]

    async def states(
        self, user_id: str, identities: list[tuple[str, str, str, str]]
    ) -> dict[tuple[str, str, str, str], str]:
        if not identities:
            return {}
        params: dict[str, Any] = {"user": user_id}
        clauses = []
        for i, (kind, entry, sense, source) in enumerate(identities):
            clauses.append(
                f"(f.entry_type=:k{i} AND f.entry_id=:e{i} "
                f"AND f.sense_id=:s{i} AND f.source_id=:o{i})"
            )
            params.update({f"k{i}": kind, f"e{i}": entry, f"s{i}": sense, f"o{i}": source})
        async with self.sessions() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT f.entry_type,f.entry_id,f.sense_id,f.source_id,f.public_id "
                            "FROM favorite_snapshot f JOIN user_account u ON u.id=f.user_id "
                            "WHERE u.public_id=:user AND u.status='ACTIVE' AND f.active=1 AND ("
                            + " OR ".join(clauses)
                            + ")"
                        ),
                        params,
                    )
                )
                .mappings()
                .all()
            )
        return {
            (
                str(row["entry_type"]),
                str(row["entry_id"]),
                str(row["sense_id"]),
                str(row["source_id"]),
            ): str(row["public_id"])
            for row in rows
        }

    async def is_snapshot_review(self, user_id: str, review_id: str) -> bool:
        async with self.sessions() as session:
            kind = await session.scalar(
                text(
                    "SELECT r.review_type FROM review_session r "
                    "JOIN user_account u ON u.id=r.user_id "
                    "WHERE u.public_id=:user AND u.status='ACTIVE' AND r.id=:review"
                ),
                {"user": user_id, "review": review_id},
            )
            return bool(kind == "FAVORITE_SNAPSHOTS")

    async def create_review(
        self, user_id: str, card_ids: list[str], key: str, now: datetime
    ) -> SnapshotReview:
        SnapshotService._key(key)
        if (
            not card_ids
            or len(card_ids) != len(set(card_ids))
            or any(not value or len(value) > 64 for value in card_ids)
        ):
            raise AppError("REVIEW_CARDS_INVALID", "复习须选择有效独立收藏", 422)
        async with self.sessions() as session, session.begin():
            owner = await lock_active_user(session, user_id)
            existing = (
                (
                    await session.execute(
                        text(
                            "SELECT * FROM review_session WHERE user_id=:user AND "
                            "idempotency_key=:key"
                        ),
                        {"user": owner, "key": key},
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                if existing["review_type"] != "FAVORITE_SNAPSHOTS":
                    raise AppError("IDEMPOTENCY_CONFLICT", "幂等键已用于旧版复习", 409)
                result = self._review_model(existing)
                if result.card_ids != card_ids:
                    raise AppError("IDEMPOTENCY_CONFLICT", "幂等键已用于其他卡片", 409)
                return result
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT public_id,snapshot_version,current_snapshot_revision_id "
                            "FROM favorite_snapshot WHERE user_id=:user AND active=1 AND "
                            "public_id IN :cards"
                        ).bindparams(bindparam("cards", expanding=True)),
                        {"user": owner, "cards": card_ids},
                    )
                )
                .mappings()
                .all()
            )
            by_id = {str(row["public_id"]): row for row in rows}
            if set(by_id) != set(card_ids):
                raise AppError("REVIEW_CARDS_INVALID", "卡片不存在或不属于本人", 422)
            cards = [
                {
                    "favorite_id": fid,
                    "snapshot_version": by_id[fid]["snapshot_version"],
                    "snapshot_revision_id": by_id[fid]["current_snapshot_revision_id"],
                }
                for fid in card_ids
            ]
            review_id = new_ulid(now)
            await session.execute(
                text(
                    "INSERT INTO review_session(id,user_id,review_type,started_at,card_count,"
                    "idempotency_key,card_ids) "
                    "VALUES(:id,:user,'FAVORITE_SNAPSHOTS',:now,:count,:key,:cards)"
                ),
                {
                    "id": review_id,
                    "user": owner,
                    "now": dbtime(now),
                    "count": len(cards),
                    "key": key,
                    "cards": json.dumps(cards),
                },
            )
            return SnapshotReview(
                id=review_id,
                card_ids=card_ids,
                snapshot_versions=[int(card["snapshot_version"]) for card in cards],
                card_count=len(cards),
                started_at=now,
            )

    async def complete_review(
        self, user_id: str, review_id: str, key: str, now: datetime
    ) -> dict[str, Any]:
        SnapshotService._key(key)
        async with self.sessions() as session, session.begin():
            owner = await lock_active_user(session, user_id)
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT * FROM review_session WHERE user_id=:user AND id=:id FOR UPDATE"
                        ),
                        {"user": owner, "id": review_id},
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise AppError("REVIEW_NOT_FOUND", "复习不存在", 404)
            if row["review_type"] != "FAVORITE_SNAPSHOTS":
                raise AppError("REVIEW_SCHEMA_CONFLICT", "请使用对应版本复习", 409)
            result = self._review_model(row)
            created = result.completed_at is None
            if created:
                frozen = json_value(row["card_ids"])
                actual = (
                    (
                        await session.execute(
                            text(
                                "SELECT public_id,snapshot_version,current_snapshot_revision_id "
                                "FROM favorite_snapshot WHERE user_id=:user AND active=1 AND "
                                "public_id IN :cards"
                            ).bindparams(bindparam("cards", expanding=True)),
                            {"user": owner, "cards": result.card_ids},
                        )
                    )
                    .mappings()
                    .all()
                )
                by_id = {str(item["public_id"]): item for item in actual}
                if any(
                    card["favorite_id"] not in by_id
                    or by_id[card["favorite_id"]]["current_snapshot_revision_id"]
                    != card["snapshot_revision_id"]
                    for card in frozen
                ):
                    raise AppError("REVIEW_SNAPSHOT_CHANGED", "收藏已取消或更新,请刷新复习", 409)
                await session.execute(
                    text(
                        "UPDATE favorite_snapshot SET last_reviewed_at="
                        "GREATEST(COALESCE(last_reviewed_at,:now),:now) "
                        "WHERE user_id=:user AND public_id IN :cards"
                    ).bindparams(bindparam("cards", expanding=True)),
                    {"now": dbtime(now), "user": owner, "cards": result.card_ids},
                )
                await session.execute(
                    text("UPDATE review_session SET completed_at=:now WHERE id=:id"),
                    {"now": dbtime(now), "id": review_id},
                )
                await session.execute(
                    text(
                        "INSERT IGNORE INTO daily_checkin(user_id,beijing_date,completion_source) "
                        "VALUES(:user,:day,'REVIEW')"
                    ),
                    {"user": owner, "day": beijing_learning_date(now)},
                )
                await append_event(
                    session,
                    event_key="review:" + review_id,
                    event_type="REVIEW_COMPLETED",
                    user_id=owner,
                    occurred_at=now,
                )
                await append_activity_events(session, owner, now)
                result = result.model_copy(update={"completed_at": now})
            return {
                "id": review_id,
                "created": created,
                "completed_at": result.completed_at,
                "checkin_date": beijing_learning_date(result.completed_at or now),
                "snapshot_schema": 2,
            }

    @staticmethod
    def _review_model(row: RowMapping) -> SnapshotReview:
        cards = json_value(row["card_ids"])
        if not isinstance(cards, list) or not cards or len(cards) != int(row["card_count"]):
            raise AppError("REVIEW_CARDS_INVALID", "复习快照集合无效", 409)
        return SnapshotReview(
            id=row["id"],
            card_count=row["card_count"],
            card_ids=[card["favorite_id"] for card in cards],
            snapshot_versions=[card["snapshot_version"] for card in cards],
            started_at=cast(datetime, utc(row["started_at"])),
            completed_at=utc(row["completed_at"]),
        )

    @staticmethod
    def _model(row: RowMapping) -> FavoriteSnapshot:
        target = row["pronunciation_target_id"]
        data = ConfirmedSnapshot(
            reference=SnapshotReference(
                content_type=row["content_type"],
                content_id=row["content_id"],
                content_version=row["saved_content_version"],
                entry_type={"VOCABULARY": "word", "PHRASE": "chunk"}.get(
                    row["entry_type"], row["entry_type"]
                ),
                entry_id=row["entry_id"],
                sense_id=row["sense_id"],
                source_id=row["source_id"],
            ),
            revision_id=row["revision_id"],
            english=row["english"],
            phonetic=row["phonetic"],
            meaning_zh=row["meaning_zh"],
            explanation=row["explanation"],
            source_sentence=row["source_sentence"],
            source_title=row["safe_title_zh"],
            source_locator=json_value(row["source_locator"]) or {},
            image_asset_id=row["image_asset_id"],
            legacy_incomplete=bool(row["legacy_incomplete"]),
            missing_fields=json_value(row["missing_fields"]) or [],
            pronunciation=PronunciationReference(
                target_id=target,
                resource_version=row["resource_version"],
                can_prepare=bool(row["allow_synthesis"])
                and row["pronunciation_status"] == "CONFIRMED",
            )
            if target
            else None,
        )
        return FavoriteSnapshot(
            favorite_id=row["public_id"],
            active=bool(row["active"]),
            snapshot_version=row["snapshot_version"],
            snapshot_revision_id=row["current_snapshot_revision_id"],
            saved_content_version=row["saved_content_version"],
            favorited_at=cast(datetime, utc(row["favorited_at"])),
            last_reviewed_at=utc(row["last_reviewed_at"]),
            data=data,
        )

    @staticmethod
    async def _reference(
        session: AsyncSession, row: FavoriteSnapshot, revision_id: str, now: datetime
    ) -> None:
        if (
            (row.data.revision_id is None or row.data.legacy_incomplete)
            and row.data.image_asset_id is None
            and row.data.pronunciation is None
        ):
            # Missing historical facts cannot form a resource reference; no fixed
            # asset is being retained or released. Preserve explicit missing data.
            return
        previous = await session.scalar(
            text(
                "SELECT sequence FROM favorite_resource_reference WHERE favorite_id=:fid FOR UPDATE"
            ),
            {"fid": row.favorite_id},
        )
        sequence = int(previous or 0) + 1
        event_id = new_ulid(now)
        ref = row.data.reference
        pronunciation = row.data.pronunciation
        payload = {
            "event_id": event_id,
            "sequence": sequence,
            "favorite_id": row.favorite_id,
            "snapshot_revision_id": revision_id,
            "active": row.active,
            "content_type": ref.content_type,
            "content_id": ref.content_id,
            "revision_id": row.data.revision_id,
            "image_asset_id": row.data.image_asset_id,
            "pronunciation_target_id": pronunciation.target_id if pronunciation else None,
            "resource_version": pronunciation.resource_version if pronunciation else None,
        }
        await session.execute(
            text(
                "INSERT INTO "
                "favorite_resource_reference(favorite_id,snapshot_revision_id,content_type,"
                "content_id,revision_id,image_asset_id,pronunciation_target_id,resource_version,"
                "active,event_id,sequence,updated_at) VALUES(:favorite_id,:snapshot_revision_id,"
                ":content_type,:content_id,:revision_id,:image_asset_id,:pronunciation_target_id,"
                ":resource_version,:active,:event_id,:sequence,:now) ON DUPLICATE KEY UPDATE "
                "snapshot_revision_id=VALUES(snapshot_revision_id),revision_id=VALUES(revision_id),"
                "image_asset_id=VALUES(image_asset_id),"
                "pronunciation_target_id=VALUES(pronunciation_target_id),"
                "resource_version=VALUES(resource_version),"
                "active=VALUES(active),event_id=VALUES(event_id),"
                "sequence=VALUES(sequence),updated_at=VALUES(updated_at)"
            ),
            payload | {"now": dbtime(now)},
        )
        await session.execute(
            text(
                "INSERT INTO miniapp_outbox(id,event_type,aggregate_id,payload) "
                "VALUES(:event,'FAVORITE_RESOURCE_REFERENCE',:fid,:payload)"
            ),
            {"event": event_id, "fid": row.favorite_id, "payload": json.dumps(payload)},
        )
