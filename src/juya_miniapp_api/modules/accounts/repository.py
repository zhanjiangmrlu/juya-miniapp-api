import asyncio
import json
from datetime import UTC, datetime
from typing import Protocol, cast

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from juya_miniapp_api.modules.accounts.domain import (
    DeletionRequest,
    DeletionStatus,
    OutboxEvent,
)
from juya_miniapp_api.shared.errors import AppError
from juya_miniapp_api.shared.ids import new_ulid


class AccountRepository(Protocol):
    async def clear_learning_data(self, user_id: str) -> None: ...

    async def request_deletion(
        self, user_id: str, requested_at: datetime, effective_at: datetime
    ) -> DeletionRequest: ...

    async def revoke_deletion(self, user_id: str, now: datetime) -> DeletionRequest: ...

    async def begin_due_deletions(self, now: datetime) -> list[DeletionRequest]: ...

    async def record_cross_domain_cleanup(
        self,
        user_id: str,
        request_id: str,
        *,
        succeeded: bool,
        now: datetime,
    ) -> DeletionRequest: ...


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


class InMemoryAccountRepository:
    def __init__(self) -> None:
        self.user_status: dict[str, str] = {}
        self.deletions: dict[str, DeletionRequest] = {}
        self.outbox: list[OutboxEvent] = []
        self.learning_rows: set[str] = set()
        self.favorite_rows: set[str] = set()
        self.review_rows: set[str] = set()
        self.checkin_rows: set[str] = set()
        self.contacts: set[str] = set()
        self.entitlements: set[str] = set()
        self._lock = asyncio.Lock()

    def seed_user(self, user_id: str) -> None:
        self.user_status[user_id] = "ACTIVE"

    async def clear_learning_data(self, user_id: str) -> None:
        async with self._lock:
            self._require_user(user_id)
            self.learning_rows.discard(user_id)
            self.favorite_rows.discard(user_id)
            self.review_rows.discard(user_id)
            self.checkin_rows.discard(user_id)

    async def request_deletion(
        self, user_id: str, requested_at: datetime, effective_at: datetime
    ) -> DeletionRequest:
        async with self._lock:
            self._require_user(user_id)
            for request in self.deletions.values():
                if request.user_id == user_id and request.status in {"PENDING", "DELETING"}:
                    return request
            if self.user_status[user_id] != "ACTIVE":
                raise AppError("ACCOUNT_STATE_INVALID", "当前账号状态不能申请注销", 409)
            request = DeletionRequest(
                new_ulid(requested_at), user_id, requested_at, effective_at, "PENDING"
            )
            self.deletions[request.id] = request
            self.user_status[user_id] = "DELETION_PENDING"
            return request

    async def revoke_deletion(self, user_id: str, now: datetime) -> DeletionRequest:
        async with self._lock:
            request = self._active_request(user_id)
            if request.status == "DELETING":
                raise AppError("DELETION_ALREADY_PROCESSING", "账号注销正在处理中", 409)
            request.status = "REVOKED"
            request.revoked_at = now
            self.user_status[user_id] = "ACTIVE"
            return request

    async def begin_due_deletions(self, now: datetime) -> list[DeletionRequest]:
        async with self._lock:
            started: list[DeletionRequest] = []
            for request in self.deletions.values():
                if request.status != "PENDING" or request.effective_at > now:
                    continue
                request.status = "DELETING"
                self.user_status[request.user_id] = "DELETING"
                self._delete_local_personal_data(request.user_id)
                self.outbox.append(
                    OutboxEvent(
                        new_ulid(now),
                        "ACCOUNT_DELETION_CLEANUP",
                        request.id,
                        {"user_id": request.user_id, "deletion_request_id": request.id},
                        "PENDING",
                        0,
                        now,
                        now,
                    )
                )
                started.append(request)
            return started

    async def record_cross_domain_cleanup(
        self,
        user_id: str,
        request_id: str,
        *,
        succeeded: bool,
        now: datetime,
    ) -> DeletionRequest:
        async with self._lock:
            request = self.deletions.get(request_id)
            if request is None or request.user_id != user_id:
                raise AppError("DELETION_NOT_FOUND", "注销申请不存在", 404)
            if request.status == "DELETED":
                return request
            if request.status != "DELETING":
                raise AppError("DELETION_STATE_INVALID", "注销状态不允许此操作", 409)
            event = self._outbox_event(request_id)
            if not succeeded:
                event.status = "PENDING"
                event.next_attempt_at = now
                return request
            event.status = "DELIVERED"
            event.processed_at = now
            request.status = "DELETED"
            request.completed_at = now
            self.user_status[user_id] = "DELETED"
            return request

    def _require_user(self, user_id: str) -> None:
        if user_id not in self.user_status:
            raise AppError("USER_NOT_FOUND", "用户不存在", 404)

    def _active_request(self, user_id: str) -> DeletionRequest:
        self._require_user(user_id)
        for request in self.deletions.values():
            if request.user_id == user_id and request.status in {"PENDING", "DELETING"}:
                return request
        raise AppError("DELETION_NOT_FOUND", "注销申请不存在", 404)

    def _outbox_event(self, request_id: str) -> OutboxEvent:
        for event in self.outbox:
            if event.aggregate_id == request_id:
                return event
        raise RuntimeError("Deletion outbox event was not found")

    def _delete_local_personal_data(self, user_id: str) -> None:
        self.learning_rows.discard(user_id)
        self.favorite_rows.discard(user_id)
        self.review_rows.discard(user_id)
        self.checkin_rows.discard(user_id)
        self.contacts.discard(user_id)


class SQLAlchemyAccountRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def clear_learning_data(self, user_id: str) -> None:
        async with self._session_factory() as session, session.begin():
            internal_id = await self._lock_user(session, user_id)
            await self._delete_learning_data(session, internal_id)

    async def request_deletion(
        self, user_id: str, requested_at: datetime, effective_at: datetime
    ) -> DeletionRequest:
        async with self._session_factory() as session, session.begin():
            internal_id = await self._lock_user(session, user_id)
            existing = await self._active_request_row(session, internal_id)
            if existing is not None:
                return self._from_row(existing, user_id)
            status = await session.scalar(
                text("SELECT status FROM user_account WHERE id = :user_id"),
                {"user_id": internal_id},
            )
            if status != "ACTIVE":
                raise AppError("ACCOUNT_STATE_INVALID", "当前账号状态不能申请注销", 409)
            request_id = new_ulid(requested_at)
            await session.execute(
                text(
                    "INSERT INTO account_deletion_request "
                    "(public_id, user_id, requested_at, effective_at, status) "
                    "VALUES (:public_id, :user_id, :requested_at, :effective_at, 'PENDING')"
                ),
                {
                    "public_id": request_id,
                    "user_id": internal_id,
                    "requested_at": _database_datetime(requested_at),
                    "effective_at": _database_datetime(effective_at),
                },
            )
            await session.execute(
                text("UPDATE user_account SET status = 'DELETION_PENDING' WHERE id = :user_id"),
                {"user_id": internal_id},
            )
            return DeletionRequest(request_id, user_id, requested_at, effective_at, "PENDING")

    async def revoke_deletion(self, user_id: str, now: datetime) -> DeletionRequest:
        async with self._session_factory() as session, session.begin():
            internal_id = await self._lock_user(session, user_id)
            row = await self._active_request_row(session, internal_id)
            if row is None:
                raise AppError("DELETION_NOT_FOUND", "注销申请不存在", 404)
            if row["status"] == "DELETING":
                raise AppError("DELETION_ALREADY_PROCESSING", "账号注销正在处理中", 409)
            await session.execute(
                text(
                    "UPDATE account_deletion_request SET status = 'REVOKED', revoked_at = :now "
                    "WHERE id = :id"
                ),
                {"now": _database_datetime(now), "id": row["id"]},
            )
            await session.execute(
                text("UPDATE user_account SET status = 'ACTIVE' WHERE id = :user_id"),
                {"user_id": internal_id},
            )
            request = self._from_row(row, user_id)
            request.status = "REVOKED"
            request.revoked_at = now
            return request

    async def begin_due_deletions(self, now: datetime) -> list[DeletionRequest]:
        async with self._session_factory() as session, session.begin():
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT d.id, d.public_id, d.user_id, u.public_id AS public_user_id, "
                            "d.requested_at, d.effective_at, d.revoked_at, d.completed_at, "
                            "d.status "
                            "FROM account_deletion_request d "
                            "JOIN user_account u ON u.id = d.user_id "
                            "WHERE d.status = 'PENDING' AND d.effective_at <= :now "
                            "ORDER BY d.id LIMIT 100 FOR UPDATE SKIP LOCKED"
                        ),
                        {"now": _database_datetime(now)},
                    )
                )
                .mappings()
                .all()
            )
            started: list[DeletionRequest] = []
            for row in rows:
                internal_id = int(row["user_id"])
                request_id = str(row["public_id"])
                public_user_id = str(row["public_user_id"])
                await session.execute(
                    text("UPDATE account_deletion_request SET status = 'DELETING' WHERE id = :id"),
                    {"id": row["id"]},
                )
                await session.execute(
                    text("UPDATE user_account SET status = 'DELETING' WHERE id = :user_id"),
                    {"user_id": internal_id},
                )
                await session.execute(
                    text(
                        "UPDATE user_session SET revoked_at = COALESCE(revoked_at, :now) "
                        "WHERE user_id = :user_id"
                    ),
                    {"user_id": internal_id, "now": _database_datetime(now)},
                )
                await self._delete_local_data(session, internal_id)
                event_id = new_ulid(now)
                await session.execute(
                    text(
                        "INSERT INTO miniapp_outbox "
                        "(id, event_type, aggregate_id, payload, status, attempt_count, "
                        "next_attempt_at, created_at) VALUES "
                        "(:id, 'ACCOUNT_DELETION_CLEANUP', :aggregate_id, :payload, "
                        "'PENDING', 0, :now, :now)"
                    ),
                    {
                        "id": event_id,
                        "aggregate_id": request_id,
                        "payload": json.dumps(
                            {
                                "user_id": public_user_id,
                                "deletion_request_id": request_id,
                            }
                        ),
                        "now": _database_datetime(now),
                    },
                )
                request = self._from_row(row, public_user_id)
                request.status = "DELETING"
                started.append(request)
            return started

    async def record_cross_domain_cleanup(
        self,
        user_id: str,
        request_id: str,
        *,
        succeeded: bool,
        now: datetime,
    ) -> DeletionRequest:
        async with self._session_factory() as session, session.begin():
            internal_id = await self._lock_user(session, user_id)
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT id, public_id, requested_at, effective_at, revoked_at, "
                            "completed_at, status FROM account_deletion_request "
                            "WHERE user_id = :user_id AND public_id = :request_id FOR UPDATE"
                        ),
                        {"user_id": internal_id, "request_id": request_id},
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise AppError("DELETION_NOT_FOUND", "注销申请不存在", 404)
            request = self._from_row(row, user_id)
            if request.status == "DELETED":
                return request
            if request.status != "DELETING":
                raise AppError("DELETION_STATE_INVALID", "注销状态不允许此操作", 409)
            if not succeeded:
                await session.execute(
                    text(
                        "UPDATE miniapp_outbox SET status = 'PENDING', next_attempt_at = :now "
                        "WHERE aggregate_id = :request_id AND event_type = "
                        "'ACCOUNT_DELETION_CLEANUP'"
                    ),
                    {"request_id": request_id, "now": _database_datetime(now)},
                )
                return request
            await session.execute(
                text(
                    "UPDATE miniapp_outbox SET status = 'DELIVERED', processed_at = :now "
                    "WHERE aggregate_id = :request_id AND event_type = "
                    "'ACCOUNT_DELETION_CLEANUP'"
                ),
                {"request_id": request_id, "now": _database_datetime(now)},
            )
            await session.execute(
                text(
                    "UPDATE account_deletion_request SET status = 'DELETED', completed_at = :now "
                    "WHERE id = :id"
                ),
                {"id": row["id"], "now": _database_datetime(now)},
            )
            await session.execute(
                text("DELETE FROM user_app_identity WHERE user_id = :user_id"),
                {"user_id": internal_id},
            )
            await session.execute(
                text("UPDATE user_account SET status = 'DELETED' WHERE id = :user_id"),
                {"user_id": internal_id},
            )
            request.status = "DELETED"
            request.completed_at = now
            return request

    async def get_deletion(self, user_id: str, request_id: str) -> DeletionRequest:
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT d.public_id, d.requested_at, d.effective_at, d.revoked_at, "
                            "d.completed_at, d.status FROM account_deletion_request d "
                            "JOIN user_account u ON u.id = d.user_id "
                            "WHERE u.public_id = :user_id AND d.public_id = :request_id"
                        ),
                        {"user_id": user_id, "request_id": request_id},
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise AppError("DELETION_NOT_FOUND", "注销申请不存在", 404)
            return self._from_row(row, user_id)

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
    async def _active_request_row(session: AsyncSession, user_id: int) -> RowMapping | None:
        return (
            (
                await session.execute(
                    text(
                        "SELECT id, public_id, requested_at, effective_at, revoked_at, "
                        "completed_at, status FROM account_deletion_request "
                        "WHERE user_id = :user_id AND status IN ('PENDING','DELETING') FOR UPDATE"
                    ),
                    {"user_id": user_id},
                )
            )
            .mappings()
            .first()
        )

    @staticmethod
    async def _delete_learning_data(session: AsyncSession, user_id: int) -> None:
        for table_name in (
            "review_session",
            "favorite_entry",
            "daily_checkin",
            "learning_completion_event",
            "learning_progress",
        ):
            await session.execute(
                text(f"DELETE FROM {table_name} WHERE user_id = :user_id"),
                {"user_id": user_id},
            )

    @classmethod
    async def _delete_local_data(cls, session: AsyncSession, user_id: int) -> None:
        await cls._delete_learning_data(session, user_id)
        for table_name in (
            "contact_correction_request",
            "contact_status_history",
            "user_contact",
            "inbox_message",
            "user_profile",
        ):
            await session.execute(
                text(f"DELETE FROM {table_name} WHERE user_id = :user_id"),
                {"user_id": user_id},
            )

    @staticmethod
    def _from_row(row: RowMapping, user_id: str) -> DeletionRequest:
        requested_at = _utc_datetime(cast(datetime, row["requested_at"]))
        effective_at = _utc_datetime(cast(datetime, row["effective_at"]))
        if requested_at is None or effective_at is None:
            raise RuntimeError("Deletion timestamps cannot be null")
        return DeletionRequest(
            str(row["public_id"]),
            user_id,
            requested_at,
            effective_at,
            cast(DeletionStatus, row["status"]),
            _utc_datetime(cast(datetime | None, row["revoked_at"])),
            _utc_datetime(cast(datetime | None, row["completed_at"])),
        )
