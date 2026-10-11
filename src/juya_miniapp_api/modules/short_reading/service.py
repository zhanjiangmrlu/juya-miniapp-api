from datetime import datetime
from typing import Protocol

from juya_miniapp_api.modules.favorites.snapshots import SnapshotService
from juya_miniapp_api.modules.short_reading.domain import (
    ReadingCompletion,
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
        progress = await self.repository.open(
            user_id,
            content_id,
            detail.content_version,
            self._paragraphs(detail),
            now,
        )
        return detail.model_copy(update={"progress": progress})

    async def save(
        self,
        user_id: str,
        content_id: str,
        position: ReadingPosition,
        now: datetime,
    ) -> ReadingProgress:
        detail = await self._authorized(user_id, content_id)
        self._position(detail, position)
        if position.client_sequence < 1:
            raise AppError("READING_SEQUENCE_INVALID", "阅读进度需要有效客户端序号", 422)
        return await self.repository.save(user_id, content_id, position, now)

    async def complete(
        self,
        user_id: str,
        content_id: str,
        key: str,
        version: int,
        now: datetime,
    ) -> ReadingCompletion:
        SnapshotService._key(key)
        replay = await self.repository.replay_completion(user_id, content_id, key, version)
        if replay:
            return replay
        detail = await self._authorized(user_id, content_id)
        self._position(detail, ReadingPosition(content_version=version))
        return await self.repository.complete(
            user_id,
            content_id,
            key,
            version,
            self._paragraphs(detail),
            now,
        )

    async def _authorized(self, user_id: str, content_id: str) -> ReadingDetail:
        detail = await self.authority.open_reading(user_id, content_id)
        if detail.content_id != content_id:
            raise AppError("CONTENT_CONTRACT_INVALID", "内容服务返回不一致", 503)
        return detail

    @staticmethod
    def _paragraphs(detail: ReadingDetail) -> dict[str, int]:
        return {row.paragraph_id: len(row.english) for row in detail.paragraphs_en}

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
