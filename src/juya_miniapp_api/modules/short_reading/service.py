from datetime import datetime
from typing import Protocol

from juya_miniapp_api.modules.favorites.snapshots import SnapshotService
from juya_miniapp_api.modules.short_reading.domain import (
    ReadingDetail,
    ReadingPosition,
    ReadingProgress,
)
from juya_miniapp_api.modules.short_reading.repository import ReadingRepository
from juya_miniapp_api.shared.errors import AppError


class ReadingAuthority(Protocol):
    async def open_reading(self, user_id: str, reading_id: str) -> ReadingDetail: ...


class ShortReadingService:
    def __init__(self, authority: ReadingAuthority, repository: ReadingRepository) -> None:
        self.authority = authority
        self.repository = repository

    async def open(self, user_id: str, content_id: str, now: datetime) -> ReadingDetail:
        detail = await self._authorized(user_id, content_id)
        old = await self.repository.get(user_id, content_id)
        position = ReadingPosition(content_version=detail.content_version)
        if old and old.saved_content_version == detail.content_version:
            candidate = ReadingPosition(
                content_version=detail.content_version,
                paragraph_id=old.paragraph_id,
                offset=old.offset,
            )
            try:
                self._position(detail, candidate)
                position = candidate
            except AppError:
                pass
        progress = await self.repository.save(user_id, content_id, position, now)
        return detail.model_copy(update={"progress": progress})

    async def save(
        self, user_id: str, content_id: str, position: ReadingPosition, now: datetime
    ) -> ReadingProgress:
        detail = await self._authorized(user_id, content_id)
        self._position(detail, position)
        return await self.repository.save(user_id, content_id, position, now)

    async def complete(
        self, user_id: str, content_id: str, key: str, version: int, now: datetime
    ) -> ReadingProgress:
        SnapshotService._key(key)
        detail = await self._authorized(user_id, content_id)
        position = ReadingPosition(content_version=version)
        self._position(detail, position)
        old = await self.repository.get(user_id, content_id)
        if old and old.saved_content_version == version:
            position = ReadingPosition(
                content_version=version, paragraph_id=old.paragraph_id, offset=old.offset
            )
        return await self.repository.save(user_id, content_id, position, now, complete=True)

    async def _authorized(self, user_id: str, content_id: str) -> ReadingDetail:
        detail = await self.authority.open_reading(user_id, content_id)
        if detail.content_id != content_id:
            raise AppError("CONTENT_CONTRACT_INVALID", "内容服务返回不一致", 503)
        return detail

    @staticmethod
    def _position(detail: ReadingDetail, position: ReadingPosition) -> None:
        if detail.content_version != position.content_version:
            raise AppError("CONTENT_VERSION_CONFLICT", "短文已更新,请重新打开", 409)
        if position.paragraph_id is None:
            if position.offset != 0:
                raise AppError("READING_POSITION_INVALID", "阅读位置不正确", 422)
            return
        paragraph = next(
            (row for row in detail.paragraphs_en if row.paragraph_id == position.paragraph_id), None
        )
        if paragraph is None or position.offset > len(paragraph.english):
            raise AppError("READING_POSITION_INVALID", "阅读位置不正确", 422)
