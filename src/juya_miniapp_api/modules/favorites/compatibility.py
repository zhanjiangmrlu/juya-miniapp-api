"""Fail-closed legacy adapter: stable references, never English-string merging."""

from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from juya_miniapp_api.integrations.admin_api.v15_content import SourceResolution
from juya_miniapp_api.integrations.admin_api.v15_facts import SQLAuthorizer
from juya_miniapp_api.integrations.admin_api.v15_guard import scene_revision
from juya_miniapp_api.modules.favorites.repository import SQLAlchemyFavoriteRepository
from juya_miniapp_api.modules.favorites.snapshots import (
    FavoriteSnapshot,
    SnapshotReference,
    SnapshotService,
)
from juya_miniapp_api.modules.favorites.snapshots_sql import (
    SQLAlchemySnapshotRepository,
    dbtime,
    lock_active_user,
)
from juya_miniapp_api.modules.learning.v15_authority import V15SceneAuthority
from juya_miniapp_api.shared.errors import AppError
from juya_miniapp_api.shared.ids import new_ulid


def legacy_snapshot(row: FavoriteSnapshot) -> dict[str, Any]:
    data, ref = row.data, row.data.reference
    return {
        "id": row.favorite_id,
        "entry_type": "VOCABULARY" if ref.entry_type == "word" else "PHRASE",
        "normalized_key": data.english,
        "entry_stable_id": ref.entry_id,
        "favorited_at": row.favorited_at,
        "last_reviewed_at": row.last_reviewed_at,
        "legacy_incomplete": data.legacy_incomplete,
        "missing_fields": data.missing_fields,
        "sources": [
            {
                "scene_id": ref.content_id,
                "sentence_snapshot": data.source_sentence or "",
                "source_locator": ref.source_id,
                "original_link": None,
                "revision_id": data.revision_id,
                "entry_version": ref.content_version,
                "entry_snapshot": {
                    "entry_id": ref.entry_id,
                    "sense_id": ref.sense_id,
                    "source_id": ref.source_id,
                    "english": data.english,
                    "phonetic": data.phonetic,
                    "chinese": data.meaning_zh,
                    "explanation": data.explanation,
                    "legacy_incomplete": data.legacy_incomplete,
                },
            }
        ],
    }


class LegacyFavoriteAdapter:
    def __init__(
        self,
        snapshots: SnapshotService,
        repository: SQLAlchemySnapshotRepository,
        legacy: SQLAlchemyFavoriteRepository,
        authority: V15SceneAuthority,
    ) -> None:
        self.snapshots, self.repository = snapshots, repository
        self.legacy, self.authority = legacy, authority

    async def create(
        self, user_id: str, payload: dict[str, Any], now: datetime, key: str | None
    ) -> dict[str, Any]:
        detail = await self.authority.open_scene(user_id, payload["scene_id"])
        if detail.revision_id != payload["revision_id"]:
            raise AppError("CONTENT_VERSION_CONFLICT", "内容已更新,请刷新后收藏", 409)
        kind = {"VOCABULARY": "word", "PHRASE": "chunk"}.get(payload["entry_type"])
        matches = [
            entry
            for entry in detail.words + detail.chunks
            if entry.entry_id == payload["entry_stable_id"] and entry.entry_type == kind
        ]
        if len(matches) != 1 or matches[0].entry_version != payload["entry_version"]:
            raise AppError("FAVORITE_REFERENCE_AMBIGUOUS", "请使用独立义项收藏接口", 409)
        entry = matches[0]
        locators = {entry.source_id, f"vocabulary:{entry.entry_id}", f"chunks:{entry.entry_id}"}
        if entry.source_locator and entry.source_locator.sentence_id:
            sid = entry.source_locator.sentence_id
            locators.update({sid, f"sentence:{sid}:entry:{entry.entry_id}"})
        if payload["source_locator"] not in locators:
            raise AppError("FAVORITE_REFERENCE_AMBIGUOUS", "来源无法唯一定位,请刷新后收藏", 409)
        ref = SnapshotReference(
            content_type=detail.content_type,
            content_id=detail.content_id,
            content_version=detail.content_version,
            entry_type=entry.entry_type,
            entry_id=entry.entry_id,
            sense_id=entry.sense_id,
            source_id=entry.source_id,
        )
        # Old clients may omit a command key. Stable identity still prevents duplicates.
        return legacy_snapshot(await self.snapshots.create(user_id, ref, key or new_ulid(now), now))

    async def resolve(self, user_id: str, favorite_id: str) -> str | None:
        async with self.repository.sessions() as session:
            return await self._resolve(session, user_id, favorite_id)

    @staticmethod
    async def _resolve(session: AsyncSession, user_id: str, favorite_id: str) -> str | None:
        direct = await session.scalar(
            text(
                "SELECT f.public_id FROM favorite_snapshot f JOIN user_account u ON u.id=f.user_id "
                "WHERE u.public_id=:user AND u.status='ACTIVE' AND f.public_id=:id"
            ),
            {"user": user_id, "id": favorite_id},
        )
        if direct is not None:
            return str(direct)
        ids: Any = (
            (
                await session.execute(
                    text(
                        "SELECT DISTINCT s.public_id FROM favorite_legacy_map m "
                        "JOIN favorite_snapshot s ON s.id=m.favorite_id "
                        "JOIN favorite_entry f ON f.id=m.legacy_favorite_id "
                        "JOIN user_account u ON u.id=f.user_id "
                        "WHERE u.public_id=:user AND u.status='ACTIVE' AND f.public_id=:id"
                    ),
                    {"user": user_id, "id": favorite_id},
                )
            )
            .scalars()
            .all()
        )
        if len(ids) > 1:
            raise AppError("FAVORITE_REFERENCE_AMBIGUOUS", "旧收藏包含多个义项,请分别操作", 409)
        return str(ids[0]) if ids else None

    async def detail(self, user_id: str, favorite_id: str) -> dict[str, Any] | None:
        resolved = await self.resolve(user_id, favorite_id)
        return legacy_snapshot(await self.snapshots.detail(user_id, resolved)) if resolved else None

    async def listing(self, user_id: str, after: str | None, limit: int) -> dict[str, Any]:
        async with self.repository.sessions() as session:
            ids: Any = (
                (
                    await session.execute(
                        text(
                            "SELECT public_id FROM (SELECT s.public_id FROM favorite_snapshot s "
                            "JOIN user_account u ON u.id=s.user_id WHERE u.public_id=:user "
                            "AND u.status='ACTIVE' AND s.active=1 UNION ALL SELECT f.public_id "
                            "FROM favorite_entry f JOIN user_account u ON u.id=f.user_id "
                            "WHERE u.public_id=:user AND u.status='ACTIVE' "
                            "AND NOT EXISTS "
                            "(SELECT 1 FROM favorite_legacy_map m "
                            "WHERE m.legacy_favorite_id=f.id)) bank "
                            "WHERE public_id>:after ORDER BY public_id LIMIT :limit"
                        ),
                        {"user": user_id, "after": after or "", "limit": limit + 1},
                    )
                )
                .scalars()
                .all()
            )
        items = []
        for fid in ids[:limit]:
            modern = await self.repository.get(user_id, str(fid))
            if modern is not None and modern.active:
                items.append(legacy_snapshot(modern))
            else:
                old = await self.legacy.get(user_id, str(fid))
                if old is not None:
                    # Only saved historical fields; never fill from today's dictionary.
                    from juya_miniapp_api.modules.favorites.router import _favorite

                    items.append(_favorite(old) | {"legacy_incomplete": True})
        return {
            "items": items,
            "has_more": len(ids) > limit,
            "next_cursor": str(ids[limit - 1]) if len(ids) > limit else None,
        }

    async def delete(self, user_id: str, favorite_id: str, now: datetime) -> None:
        async with self.repository.sessions() as session, session.begin():
            owner = await lock_active_user(session, user_id)
            old_id = await session.scalar(
                text(
                    "SELECT id FROM favorite_entry WHERE user_id=:user AND public_id=:id FOR UPDATE"
                ),
                {"user": owner, "id": favorite_id},
            )
            if old_id is not None:
                # Backfill locks these same rows; a late migration cannot resurrect a deletion.
                sources: Any = (
                    (
                        await session.execute(
                            text("SELECT id FROM favorite_source WHERE favorite_id=:id FOR UPDATE"),
                            {"id": old_id},
                        )
                    )
                    .scalars()
                    .all()
                )
                if len(sources) > 1:
                    raise AppError(
                        "FAVORITE_REFERENCE_AMBIGUOUS", "旧收藏包含多个来源,请分别操作", 409
                    )
            resolved = await self._resolve(session, user_id, favorite_id)
            if resolved is not None:
                row = await self.repository._get(session, user_id, resolved)
                if row is not None and row.active:
                    await session.execute(
                        text(
                            "UPDATE favorite_snapshot SET active=0,cancelled_at=:now "
                            "WHERE user_id=:user AND public_id=:id"
                        ),
                        {"now": dbtime(now), "user": owner, "id": resolved},
                    )
                    await self.repository._reference(
                        session,
                        row.model_copy(update={"active": False}),
                        str(row.snapshot_revision_id),
                        now,
                    )
            elif old_id is not None:
                await session.execute(
                    text("DELETE FROM favorite_entry WHERE id=:id"), {"id": old_id}
                )
            # Missing/deleted ids are idempotently successful, with no cross-user effect.

    async def review_ids(self, user_id: str, ids: list[str]) -> list[str]:
        resolved = [await self.resolve(user_id, fid) for fid in ids]
        if any(fid is None for fid in resolved):
            raise AppError("FAVORITE_MIGRATION_REQUIRED", "历史收藏尚未完成迁移,请稍后刷新", 409)
        return [str(fid) for fid in resolved]

    async def source_resolution(self, user_id: str, favorite_id: str) -> SourceResolution:
        row = await self.snapshots.detail(user_id, favorite_id)
        ref = row.data.reference
        if ref.content_type != "SCENE":
            return await self.authority.client.source_resolution(user_id, favorite_id)
        target_version: int | None = None
        try:
            async with self.repository.sessions() as session:
                _, version, content = await scene_revision(session, ref.content_id)
                kind: Literal["word_card", "dialogue"] = (
                    "word_card"
                    if content.template_type in {"word_card", "vocabulary"}
                    else "dialogue"
                )
                facts = await SQLAuthorizer()(
                    session, user_id, kind, ref.content_id, datetime.now(UTC), lock=False
                )
                if facts.full_access:
                    target_version = version
        except AppError as error:
            if error.status_code not in {403, 404, 409}:
                raise
        return SourceResolution(
            saved_content_version=row.saved_content_version,
            target_content_version=target_version,
            status="UPDATED_OR_UNRESOLVED" if target_version else "NO_ACCESS",
            safe_summary={"title_zh": row.data.source_title or ""},
        )
