from datetime import UTC, datetime
from typing import Literal

from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from juya_miniapp_api.integrations.admin_api.client import _validate
from juya_miniapp_api.integrations.admin_api.schemas import SignedResource
from juya_miniapp_api.integrations.admin_api.v15_client import AdminV15Client
from juya_miniapp_api.integrations.admin_api.v15_facts import SQLAuthorizer
from juya_miniapp_api.modules.favorites.snapshots import StrictModel, request_digest
from juya_miniapp_api.modules.short_reading.cover import current_cover_url
from juya_miniapp_api.modules.short_reading.domain import Paragraph, ReadingDetail, ReadingPreview
from juya_miniapp_api.modules.short_reading.order import ReadingListCursor
from juya_miniapp_api.shared.errors import AppError


class InternalPreview(StrictModel):
    content_id: str
    content_type: Literal["short_reading"]
    content_version: int = Field(ge=1)
    title_en: str
    title_zh: str
    category: str
    topic: str
    level: str
    safe_intro: str
    cover_asset_id: str | None


class V15ReadingAuthority:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], client: AdminV15Client) -> None:
        self.sessions = sessions
        self.client = client

    async def access(self, user_id: str, identity: str) -> Literal["TRIAL", "FORMAL", "PREVIEW"]:
        async with self.sessions() as session:
            facts = await SQLAuthorizer()(
                session, user_id, "short_reading", identity, datetime.now(UTC), lock=False
            )
            if not facts.visible:
                raise AppError("CONTENT_ACCESS_DENIED", "短文入口当前不可用", 403)
            if not facts.full_access:
                return "PREVIEW"
            trial = await session.scalar(
                text("SELECT enabled FROM short_reading_trial WHERE short_reading_id=:id"),
                {"id": identity},
            )
            return "TRIAL" if trial else "FORMAL"

    async def preview(self, user_id: str, identity: str) -> ReadingPreview:
        raw = _validate(
            InternalPreview, await self.client.preview(user_id, "short_reading", identity)
        )
        if raw.content_id != identity:
            raise AppError("CONTENT_CONTRACT_INVALID", "短文标识不一致", 503)
        cover = None
        if raw.cover_asset_id is not None:
            cover = await self.client.preview_cover(user_id, "short_reading", identity)
            current_cover_url(cover, raw.cover_asset_id, datetime.now(UTC))
            current = _validate(
                InternalPreview, await self.client.preview(user_id, "short_reading", identity)
            )
            if current != raw:
                raise AppError("CONTENT_VERSION_CONFLICT", "短文已更新,请刷新预览", 409)
        access = await self.access(user_id, identity)
        # Recheck expiry after both live preview and local module/account authorization.
        cover_url = (
            current_cover_url(cover, raw.cover_asset_id, datetime.now(UTC))
            if raw.cover_asset_id is not None
            else None
        )
        return ReadingPreview(
            **raw.model_dump(exclude={"content_version", "safe_intro", "cover_asset_id"}),
            introduction=raw.safe_intro,
            access=access,
            cover_url=cover_url,
        )

    async def listing(self, user_id: str, cursor: str | None, limit: int) -> dict[str, object]:
        if not 1 <= limit <= 100:
            raise AppError("PAGINATION_INVALID", "分页参数无效", 422)
        async with self.sessions() as session:
            allowed = await session.scalar(
                text(
                    "SELECT u.id FROM user_account u JOIN learning_module_config m "
                    "ON m.module_type='short_reading' AND m.enabled=1 "
                    "WHERE u.public_id=:user AND u.status='ACTIVE'"
                ),
                {"user": user_id},
            )
            if allowed is None:
                raise AppError("CONTENT_ACCESS_DENIED", "短文入口当前不可用", 403)
        rows = await self._order_snapshot()
        version = request_digest(rows)
        after = ReadingListCursor.decode(user_id, version, cursor) if cursor else None
        candidates = [
            row
            for row in rows
            if after is None or (row[0], row[1]) > (after.sort_order, after.content_id)
        ]
        items = []
        for _, identity, _ in candidates[:limit]:
            try:
                items.append((await self.preview(user_id, str(identity))).model_dump(mode="json"))
            except AppError as error:
                if error.status_code != 403:
                    raise
        if request_digest(await self._order_snapshot()) != version:
            raise AppError("CURSOR_INVALID", "内容顺序已变化,请刷新", 422)
        has_more = len(candidates) > limit
        next_cursor = (
            ReadingListCursor(candidates[limit - 1][0], candidates[limit - 1][1]).encode(
                user_id, version
            )
            if has_more
            else None
        )
        return {"items": items, "has_more": has_more, "next_cursor": next_cursor}

    async def _order_snapshot(self) -> list[tuple[int, str, str | None]]:
        # The shared admin-owned order is authoritative. Its content digest also
        # invalidates cursors on reorders/publication changes without guessed config keys.
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT sort_order,public_id,published_revision_id FROM short_reading "
                        "WHERE status='PUBLISHED' ORDER BY sort_order ASC,public_id ASC"
                    )
                )
            ).all()
        return [(int(row[0]), str(row[1]), row[2]) for row in rows]

    async def open_reading(self, user_id: str, identity: str) -> ReadingDetail:
        raw = await self.client.reading_detail(user_id, identity)
        access = await self.access(user_id, identity)
        if access == "PREVIEW":
            raise AppError("CONTENT_ACCESS_DENIED", "当前账号暂未开通此内容", 403)
        if raw.content_id != identity or len(raw.paragraphs_en) != len(raw.paragraph_ids):
            raise AppError("CONTENT_CONTRACT_INVALID", "短文段落契约不一致", 503)
        return _validate(
            ReadingDetail,
            {
                **raw.model_dump(
                    exclude={"safe_intro", "paragraph_ids", "cover_asset_id", "paragraphs_en"}
                ),
                "introduction": raw.safe_intro,
                "access": access,
                "paragraphs_en": [
                    Paragraph(paragraph_id=pid, english=english).model_dump()
                    for pid, english in zip(raw.paragraph_ids, raw.paragraphs_en, strict=True)
                ],
            },
        )

    async def resource(
        self, user_id: str, identity: str, target: str, revision: str
    ) -> SignedResource:
        detail = await self.open_reading(user_id, identity)
        audio = detail.whole_audio
        if detail.revision_id != revision:
            raise AppError("CONTENT_VERSION_CONFLICT", "短文已更新,请重新打开", 409)
        if audio is None or audio.target_id != target:
            raise AppError("RESOURCE_NOT_FOUND", "短文音频不存在", 404)
        payload = await self.client.resource(
            user_id, "short_reading", identity, target, audio.resource_version
        )
        result = _validate(
            SignedResource,
            {
                "resource_id": payload.get("target_id", target),
                "url": payload.get("url"),
                "expires_at": payload.get("expires_at"),
            },
        )
        if result.resource_id != target or datetime.now(UTC) >= result.expires_at.astimezone(UTC):
            raise AppError("SIGNED_MEDIA_EXPIRED", "媒体地址已失效", 503)
        return result
