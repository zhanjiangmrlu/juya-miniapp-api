import asyncio
from datetime import datetime
from typing import Protocol

from juya_miniapp_api.modules.favorites.snapshots import request_digest
from juya_miniapp_api.modules.short_reading.domain import (
    ReadingCompletion,
    ReadingPosition,
    ReadingProgress,
)
from juya_miniapp_api.shared.errors import AppError


def completion_key(content_id: str, key: str) -> str:
    return request_digest([content_id, key])


def completion_digest(content_id: str, version: int) -> str:
    return request_digest([content_id, version])


def preserved_position(
    old: ReadingProgress | None,
    version: int,
    paragraphs: dict[str, int],
) -> ReadingPosition:
    if old is None or old.saved_content_version != version:
        return ReadingPosition(content_version=version)
    candidate = ReadingPosition(
        content_version=version,
        paragraph_id=old.paragraph_id,
        offset=old.offset,
        client_sequence=old.client_sequence,
    )
    if (candidate.paragraph_id is None and candidate.offset == 0) or (
        candidate.paragraph_id in paragraphs
        and candidate.offset <= paragraphs[candidate.paragraph_id]
    ):
        return candidate
    return ReadingPosition(content_version=version, client_sequence=old.client_sequence)


def next_progress(
    content_id: str,
    old: ReadingProgress | None,
    position: ReadingPosition,
    now: datetime,
    *,
    complete: bool = False,
) -> ReadingProgress:
    return ReadingProgress(
        content_id=content_id,
        saved_content_version=position.content_version,
        paragraph_id=position.paragraph_id,
        offset=position.offset,
        client_sequence=position.client_sequence,
        started_at=old.started_at if old else now,
        last_read_at=max(old.last_read_at, now) if old else now,
        completed_at=(old.completed_at if old else None) or (now if complete else None),
    )


class ReadingRepository(Protocol):
    async def get(self, user_id: str, content_id: str) -> ReadingProgress | None: ...

    async def open(
        self,
        user_id: str,
        content_id: str,
        version: int,
        paragraphs: dict[str, int],
        now: datetime,
    ) -> ReadingProgress: ...

    async def save(
        self,
        user_id: str,
        content_id: str,
        position: ReadingPosition,
        now: datetime,
    ) -> ReadingProgress: ...

    async def replay_completion(
        self,
        user_id: str,
        content_id: str,
        key: str,
        version: int,
    ) -> ReadingCompletion | None: ...

    async def complete(
        self,
        user_id: str,
        content_id: str,
        key: str,
        version: int,
        paragraphs: dict[str, int],
        now: datetime,
    ) -> ReadingCompletion: ...


class InMemoryReadingRepository:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], ReadingProgress] = {}
        self.operations: dict[tuple[str, str], tuple[str, ReadingCompletion]] = {}
        self.completion_events: list[tuple[str, str]] = []
        self._lock = asyncio.Lock()

    async def get(self, user_id: str, content_id: str) -> ReadingProgress | None:
        return self.rows.get((user_id, content_id))

    async def open(
        self,
        user_id: str,
        content_id: str,
        version: int,
        paragraphs: dict[str, int],
        now: datetime,
    ) -> ReadingProgress:
        async with self._lock:
            old = self.rows.get((user_id, content_id))
            position = preserved_position(old, version, paragraphs)
            row = next_progress(content_id, old, position, now)
            self.rows[user_id, content_id] = row
            return row

    async def save(
        self,
        user_id: str,
        content_id: str,
        position: ReadingPosition,
        now: datetime,
    ) -> ReadingProgress:
        async with self._lock:
            old = self.rows.get((user_id, content_id))
            if (
                old
                and old.saved_content_version == position.content_version
                and (position.client_sequence <= old.client_sequence)
            ):
                return old
            row = next_progress(content_id, old, position, now)
            self.rows[user_id, content_id] = row
            return row

    async def replay_completion(
        self,
        user_id: str,
        content_id: str,
        key: str,
        version: int,
    ) -> ReadingCompletion | None:
        record = self.operations.get((user_id, completion_key(content_id, key)))
        if record is None:
            return None
        if record[0] != completion_digest(content_id, version):
            raise AppError("IDEMPOTENCY_CONFLICT", "幂等键已用于其他完成请求", 409)
        return record[1]

    async def complete(
        self,
        user_id: str,
        content_id: str,
        key: str,
        version: int,
        paragraphs: dict[str, int],
        now: datetime,
    ) -> ReadingCompletion:
        async with self._lock:
            replay = await self.replay_completion(user_id, content_id, key, version)
            if replay:
                return replay
            old = self.rows.get((user_id, content_id))
            created = old is None or old.completed_at is None
            position = preserved_position(old, version, paragraphs)
            row = next_progress(content_id, old, position, now, complete=True)
            self.rows[user_id, content_id] = row
            if created:
                self.completion_events.append((user_id, content_id))
            result = ReadingCompletion(**row.model_dump(), created=created)
            self.operations[user_id, completion_key(content_id, key)] = (
                completion_digest(content_id, version),
                result,
            )
            return result
