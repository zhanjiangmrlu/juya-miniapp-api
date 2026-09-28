import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from juya_miniapp_api.infrastructure.observability.metrics import DELETION_FAILURES
from juya_miniapp_api.modules.accounts.domain import OutboxEvent, OutboxStatus

OutboxHandler = Callable[[OutboxEvent], Awaitable[None]]
PROCESSING_LEASE = timedelta(minutes=5)


class OutboxStore(Protocol):
    async def claim_due(self, now: datetime, limit: int) -> list[OutboxEvent]: ...

    async def mark_delivered(self, event_id: str, now: datetime) -> None: ...

    async def mark_failed(
        self,
        event_id: str,
        *,
        attempt_count: int,
        next_attempt_at: datetime | None,
        dead: bool,
    ) -> None: ...


class OutboxDispatcher:
    def __init__(
        self,
        store: OutboxStore,
        handler: OutboxHandler,
        *,
        max_attempts: int = 8,
        base_delay: timedelta = timedelta(seconds=30),
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._store = store
        self._handler = handler
        self._max_attempts = max_attempts
        self._base_delay = base_delay

    async def dispatch_due(self, now: datetime, *, limit: int = 100) -> int:
        events = await self._store.claim_due(now, limit)
        for event in events:
            try:
                await self._handler(event)
            except Exception:
                attempts = event.attempt_count + 1
                dead = attempts >= self._max_attempts
                if dead and event.event_type == "ACCOUNT_DELETION_CLEANUP":
                    DELETION_FAILURES.labels(stage="cross_domain_cleanup").inc()
                next_attempt_at = None
                if not dead:
                    next_attempt_at = now + self._base_delay * (2 ** (attempts - 1))
                await self._store.mark_failed(
                    event.id,
                    attempt_count=attempts,
                    next_attempt_at=next_attempt_at,
                    dead=dead,
                )
            else:
                await self._store.mark_delivered(event.id, now)
        return len(events)


class InMemoryOutboxStore:
    def __init__(self, events: Sequence[OutboxEvent] = ()) -> None:
        self.events = {event.id: event for event in events}
        self._lock = asyncio.Lock()

    async def claim_due(self, now: datetime, limit: int) -> list[OutboxEvent]:
        async with self._lock:
            claimed: list[OutboxEvent] = []
            for event in self.events.values():
                due = event.next_attempt_at is None or event.next_attempt_at <= now
                if event.status in {"PENDING", "FAILED", "PROCESSING"} and due:
                    event.status = "PROCESSING"
                    event.next_attempt_at = now + PROCESSING_LEASE
                    claimed.append(event)
                    if len(claimed) >= limit:
                        break
            return claimed

    async def mark_delivered(self, event_id: str, now: datetime) -> None:
        async with self._lock:
            event = self.events[event_id]
            event.status = "DELIVERED"
            event.processed_at = now
            event.next_attempt_at = None

    async def mark_failed(
        self,
        event_id: str,
        *,
        attempt_count: int,
        next_attempt_at: datetime | None,
        dead: bool,
    ) -> None:
        async with self._lock:
            event = self.events[event_id]
            if event.status != "PROCESSING":
                return
            event.status = "DEAD" if dead else "FAILED"
            event.attempt_count = attempt_count
            event.next_attempt_at = next_attempt_at


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


class SQLAlchemyOutboxStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def claim_due(self, now: datetime, limit: int) -> list[OutboxEvent]:
        async with self._session_factory() as session, session.begin():
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT id, event_type, aggregate_id, payload, status, "
                            "attempt_count, next_attempt_at, created_at, processed_at "
                            "FROM miniapp_outbox WHERE status IN "
                            "('PENDING','FAILED','PROCESSING') "
                            "AND (next_attempt_at IS NULL OR next_attempt_at <= :now) "
                            "ORDER BY created_at LIMIT :limit FOR UPDATE SKIP LOCKED"
                        ),
                        {"now": _database_datetime(now), "limit": limit},
                    )
                )
                .mappings()
                .all()
            )
            ids = [str(row["id"]) for row in rows]
            for event_id in ids:
                await session.execute(
                    text(
                        "UPDATE miniapp_outbox SET status = 'PROCESSING', "
                        "next_attempt_at = :lease_until WHERE id = :event_id"
                    ),
                    {
                        "event_id": event_id,
                        "lease_until": _database_datetime(now + PROCESSING_LEASE),
                    },
                )
            return [self._from_row(row) for row in rows]

    async def mark_delivered(self, event_id: str, now: datetime) -> None:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE miniapp_outbox SET status = 'DELIVERED', processed_at = :now, "
                    "next_attempt_at = NULL WHERE id = :event_id"
                ),
                {"event_id": event_id, "now": _database_datetime(now)},
            )

    async def mark_failed(
        self,
        event_id: str,
        *,
        attempt_count: int,
        next_attempt_at: datetime | None,
        dead: bool,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE miniapp_outbox SET status = :status, attempt_count = :attempt_count, "
                    "next_attempt_at = :next_attempt_at WHERE id = :event_id "
                    "AND status = 'PROCESSING'"
                ),
                {
                    "event_id": event_id,
                    "status": "DEAD" if dead else "FAILED",
                    "attempt_count": attempt_count,
                    "next_attempt_at": (
                        None if next_attempt_at is None else _database_datetime(next_attempt_at)
                    ),
                },
            )

    @staticmethod
    def _from_row(row: object) -> OutboxEvent:
        mapping = cast(dict[str, object], row)
        raw_payload = mapping["payload"]
        payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        if not isinstance(payload, dict):
            raise RuntimeError("Outbox payload must be an object")
        created_at = _utc_datetime(cast(datetime, mapping["created_at"]))
        if created_at is None:
            raise RuntimeError("Outbox created_at cannot be null")
        return OutboxEvent(
            str(mapping["id"]),
            str(mapping["event_type"]),
            str(mapping["aggregate_id"]),
            cast(dict[str, object], payload),
            cast(OutboxStatus, mapping["status"]),
            int(cast(int, mapping["attempt_count"])),
            _utc_datetime(cast(datetime | None, mapping["next_attempt_at"])),
            created_at,
            _utc_datetime(cast(datetime | None, mapping["processed_at"])),
        )
