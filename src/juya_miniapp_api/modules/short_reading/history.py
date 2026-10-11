"""Owned safe reading summaries remain available after source access is withdrawn."""

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from juya_miniapp_api.integrations.admin_api.v15_facts import SQLAuthorizer
from juya_miniapp_api.modules.favorites.snapshots import StrictModel, request_digest
from juya_miniapp_api.modules.favorites.snapshots_sql import dbtime, utc
from juya_miniapp_api.shared.errors import AppError


class ReadingHistoryItem(StrictModel):
    content_id: str
    title_en: str
    title_zh: str
    saved_content_version: int = Field(ge=1)
    started_at: datetime
    last_read_at: datetime
    completed_at: datetime | None
    can_open: bool


class ReadingHistoryPage(StrictModel):
    items: list[ReadingHistoryItem]
    started_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    next_cursor: str | None
    has_more: bool


@dataclass(frozen=True)
class HistoryCursor:
    last_read_at: datetime
    content_id: str

    def encode(self, user_id: str) -> str:
        return base64.urlsafe_b64encode(
            json.dumps(
                {
                    "scope": request_digest([user_id, "reading-history", 1]),
                    "last": self.last_read_at.isoformat(),
                    "id": self.content_id,
                }
            ).encode()
        ).decode()

    @classmethod
    def decode(cls, user_id: str, value: str) -> "HistoryCursor":
        try:
            if len(value) > 2048:
                raise ValueError("length")
            data = json.loads(base64.b64decode(value, altchars=b"-_", validate=True))
            if (
                data["scope"] != request_digest([user_id, "reading-history", 1])
                or not isinstance(data["id"], str)
                or not 0 < len(data["id"]) <= 64
            ):
                raise ValueError("scope")
            last = datetime.fromisoformat(data["last"])
            if last.tzinfo is None:
                raise ValueError("timezone")
            return cls(last, data["id"])
        except (ValueError, TypeError, KeyError) as error:
            raise AppError("CURSOR_INVALID", "分页位置已失效,请刷新", 422) from error


class SQLReadingHistory:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def page(
        self, user_id: str, cursor: str | None, limit: int, now: datetime
    ) -> ReadingHistoryPage:
        if not 1 <= limit <= 100:
            raise AppError("PAGINATION_INVALID", "分页参数无效", 422)
        after = HistoryCursor.decode(user_id, cursor) if cursor else None
        async with self.sessions() as session:
            owner = await session.scalar(
                text("SELECT id FROM user_account WHERE public_id=:user AND status='ACTIVE'"),
                {"user": user_id},
            )
            if owner is None:
                raise AppError("ACCOUNT_NOT_ACTIVE", "账号当前不可用", 403)
            counts = (
                (
                    await session.execute(
                        text(
                            "SELECT COUNT(*) AS started,COUNT(completed_at) AS completed "
                            "FROM short_reading_progress WHERE user_id=:user"
                        ),
                        {"user": owner},
                    )
                )
                .mappings()
                .one()
            )
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT p.short_reading_id,p.saved_content_version,p.started_at,"
                            "p.last_read_at,p.completed_at,"
                            "JSON_UNQUOTE(JSON_EXTRACT(r.content_snapshot,'$.title_en')) "
                            "AS title_en,"
                            "JSON_UNQUOTE(JSON_EXTRACT(r.content_snapshot,'$.title_zh')) "
                            "AS title_zh "
                            "FROM short_reading_progress p LEFT JOIN short_reading s "
                            "ON s.public_id=p.short_reading_id LEFT JOIN short_reading_revision r "
                            "ON r.public_id=s.published_revision_id AND r.status='PUBLISHED' "
                            "WHERE p.user_id=:user AND (:after IS NULL OR p.last_read_at<:after "
                            "OR (p.last_read_at=:after AND p.short_reading_id>:id)) "
                            "ORDER BY p.last_read_at DESC,p.short_reading_id ASC LIMIT :limit"
                        ),
                        {
                            "user": owner,
                            "after": dbtime(after.last_read_at) if after else None,
                            "id": after.content_id if after else "",
                            "limit": limit + 1,
                        },
                    )
                )
                .mappings()
                .all()
            )
            items = []
            for row in rows[:limit]:
                facts = await SQLAuthorizer()(
                    session,
                    user_id,
                    "short_reading",
                    row["short_reading_id"],
                    now,
                    lock=False,
                )
                items.append(
                    ReadingHistoryItem(
                        content_id=row["short_reading_id"],
                        title_en=row["title_en"] or "",
                        title_zh=row["title_zh"] or "短文已下线",
                        saved_content_version=row["saved_content_version"],
                        started_at=cast(datetime, utc(row["started_at"])),
                        last_read_at=cast(datetime, utc(row["last_read_at"])),
                        completed_at=utc(row["completed_at"]),
                        can_open=facts.full_access,
                    )
                )
        has_more = len(rows) > limit
        tail = items[-1] if has_more else None
        return ReadingHistoryPage(
            items=items,
            started_count=counts["started"],
            completed_count=counts["completed"],
            has_more=has_more,
            next_cursor=HistoryCursor(tail.last_read_at, tail.content_id).encode(user_id)
            if tail
            else None,
        )
