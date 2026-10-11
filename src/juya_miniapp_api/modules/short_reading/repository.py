import asyncio
from datetime import datetime
from typing import Protocol

from juya_miniapp_api.modules.short_reading.domain import ReadingPosition, ReadingProgress


class ReadingRepository(Protocol):
    async def get(self, user_id: str, content_id: str) -> ReadingProgress | None: ...

    async def save(
        self,
        user_id: str,
        content_id: str,
        position: ReadingPosition,
        now: datetime,
        *,
        complete: bool = False,
    ) -> ReadingProgress: ...


class InMemoryReadingRepository:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], ReadingProgress] = {}
        self.completion_events: list[tuple[str, str]] = []
        self._lock = asyncio.Lock()

    async def get(self, user_id: str, content_id: str) -> ReadingProgress | None:
        return self.rows.get((user_id, content_id))

    async def save(
        self,
        user_id: str,
        content_id: str,
        position: ReadingPosition,
        now: datetime,
        *,
        complete: bool = False,
    ) -> ReadingProgress:
        async with self._lock:
            old = self.rows.get((user_id, content_id))
            completion = old.completed_at if old else None
            if complete and completion is None:
                completion = now
                self.completion_events.append((user_id, content_id))
            row = ReadingProgress(
                content_id=content_id,
                saved_content_version=position.content_version,
                paragraph_id=position.paragraph_id,
                offset=position.offset,
                started_at=old.started_at if old else now,
                last_read_at=now,
                completed_at=completion,
            )
            self.rows[user_id, content_id] = row
            return row
