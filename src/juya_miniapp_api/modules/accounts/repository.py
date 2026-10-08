import asyncio
import json
from datetime import UTC, datetime
from typing import Protocol, cast

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from juya_miniapp_api.infrastructure.analytics_events import append_event
from juya_miniapp_api.modules.accounts.domain import (
    DeletionRequest,
    DeletionStatus,
    OutboxEvent,
)
from juya_miniapp_api.shared.errors import AppError
from juya_miniapp_api.shared.ids import new_ulid


class AccountRepository(Protocol):
    async def clear_learning_data(self, user_id: str) -> None:
        # 功能:清空用户学习记录、收藏、复习和打卡数据
        # 参数:
        #     self: 当前账号注销与清理仓库实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:无返回值。
        ...

    async def request_deletion(
        self, user_id: str, requested_at: datetime, effective_at: datetime
    ) -> DeletionRequest:
        # 功能:幂等创建账号注销申请并设置七天等待期
        # 参数:
        #     self: 当前账号注销与清理仓库实例
        #     user_id: 当前操作所属用户的公开标识
        #     requested_at: 账号注销申请首次提交的时间
        #     effective_at: 账号注销等待期结束并允许执行的时间
        # 返回:账号注销申请及执行状态
        ...

    async def revoke_deletion(self, user_id: str, now: datetime) -> DeletionRequest:
        # 功能:撤回等待期内的账号注销申请
        # 参数:
        #     self: 当前账号注销与清理仓库实例
        #     user_id: 当前操作所属用户的公开标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:账号注销申请及执行状态
        ...

    async def begin_due_deletions(self, now: datetime) -> list[DeletionRequest]:
        # 功能:锁定到期注销申请并启动本地清理与跨域清理事件
        # 参数:
        #     self: 当前账号注销与清理仓库实例
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:账号注销申请及执行状态集合
        ...

    async def record_cross_domain_cleanup(
        self,
        user_id: str,
        request_id: str,
        *,
        succeeded: bool,
        now: datetime,
    ) -> DeletionRequest:
        # 功能:记录跨域清理回执并在全部成功后完成账号注销
        # 参数:
        #     self: 当前账号注销与清理仓库实例
        #     user_id: 当前操作所属用户的公开标识
        #     request_id: 账号注销申请的公开标识,用于匹配跨域清理回执
        #     succeeded: 管理端跨域清理是否已确认成功
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:账号注销申请及执行状态
        ...


def _database_datetime(value: datetime) -> datetime:
    # 功能:将时间转换为数据库保存的无时区UTC时间
    # 参数:
    #     value: 待转换时区的必填数据库或业务时间
    # 返回:转换后的无时区UTC时间
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _utc_datetime(value: datetime | None) -> datetime | None:
    # 功能:将数据库时间统一为带UTC时区的时间并保留空值
    # 参数:
    #     value: 待转换时区的数据库或业务时间;空值保留为空
    # 返回:带UTC时区的时间;原值为空时返回None
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class InMemoryAccountRepository:
    def __init__(self) -> None:
        # 功能:初始化账号注销的InMemoryAccountRepository对象的状态存储
        # 参数:
        #     self: 当前账号注销的InMemoryAccountRepository实例
        # 返回:无返回值。
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
        # 功能:在内存账号仓库中初始化有效用户
        # 参数:
        #     self: 当前账号注销的InMemoryAccountRepository实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:无返回值。
        self.user_status[user_id] = "ACTIVE"

    async def clear_learning_data(self, user_id: str) -> None:
        # 功能:清空用户学习记录、收藏、复习和打卡数据
        # 参数:
        #     self: 当前账号注销的InMemoryAccountRepository实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:无返回值。
        async with self._lock:
            self._require_user(user_id)
            self.learning_rows.discard(user_id)
            self.favorite_rows.discard(user_id)
            self.review_rows.discard(user_id)
            self.checkin_rows.discard(user_id)

    async def request_deletion(
        self, user_id: str, requested_at: datetime, effective_at: datetime
    ) -> DeletionRequest:
        # 功能:幂等创建账号注销申请并设置七天等待期
        # 参数:
        #     self: 当前账号注销的InMemoryAccountRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     requested_at: 账号注销申请首次提交的时间
        #     effective_at: 账号注销等待期结束并允许执行的时间
        # 返回:账号注销申请及执行状态
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
        # 功能:撤回等待期内的账号注销申请
        # 参数:
        #     self: 当前账号注销的InMemoryAccountRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:账号注销申请及执行状态
        async with self._lock:
            request = self._active_request(user_id)
            if request.status == "DELETING":
                raise AppError("DELETION_ALREADY_PROCESSING", "账号注销正在处理中", 409)
            request.status = "REVOKED"
            request.revoked_at = now
            self.user_status[user_id] = "ACTIVE"
            return request

    async def begin_due_deletions(self, now: datetime) -> list[DeletionRequest]:
        # 功能:锁定到期注销申请并启动本地清理与跨域清理事件
        # 参数:
        #     self: 当前账号注销的InMemoryAccountRepository实例
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:账号注销申请及执行状态集合
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
        # 功能:记录跨域清理回执并在全部成功后完成账号注销
        # 参数:
        #     self: 当前账号注销的InMemoryAccountRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     request_id: 账号注销申请的公开标识,用于匹配跨域清理回执
        #     succeeded: 管理端跨域清理是否已确认成功
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:账号注销申请及执行状态
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
        # 功能:校验内存账号仓库中是否存在指定用户
        # 参数:
        #     self: 当前账号注销的InMemoryAccountRepository实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:无返回值。
        if user_id not in self.user_status:
            raise AppError("USER_NOT_FOUND", "用户不存在", 404)

    def _active_request(self, user_id: str) -> DeletionRequest:
        # 功能:查找用户仍处于等待或执行状态的注销申请
        # 参数:
        #     self: 当前账号注销的InMemoryAccountRepository实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:账号注销申请及执行状态
        self._require_user(user_id)
        for request in self.deletions.values():
            if request.user_id == user_id and request.status in {"PENDING", "DELETING"}:
                return request
        raise AppError("DELETION_NOT_FOUND", "注销申请不存在", 404)

    def _outbox_event(self, request_id: str) -> OutboxEvent:
        # 功能:组装账号注销跨域清理的发件箱事件
        # 参数:
        #     self: 当前账号注销的InMemoryAccountRepository实例
        #     request_id: 账号注销申请的公开标识,用于匹配跨域清理回执
        # 返回:跨域清理发件箱事件
        for event in self.outbox:
            if event.aggregate_id == request_id:
                return event
        raise RuntimeError("Deletion outbox event was not found")

    def _delete_local_personal_data(self, user_id: str) -> None:
        # 功能:清理内存中的用户学习数据与联系方式
        # 参数:
        #     self: 当前账号注销的InMemoryAccountRepository实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:无返回值。
        self.learning_rows.discard(user_id)
        self.favorite_rows.discard(user_id)
        self.review_rows.discard(user_id)
        self.checkin_rows.discard(user_id)
        self.contacts.discard(user_id)


class SQLAlchemyAccountRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        # 功能:初始化账号注销的SQLAlchemyAccountRepository对象并保存所需依赖与配置
        # 参数:
        #     self: 当前账号注销的SQLAlchemyAccountRepository实例
        #     session_factory: 创建数据库事务会话的异步工厂
        # 返回:无返回值。
        self._session_factory = session_factory

    async def clear_learning_data(self, user_id: str) -> None:
        # 功能:清空用户学习记录、收藏、复习和打卡数据
        # 参数:
        #     self: 当前账号注销的SQLAlchemyAccountRepository实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:无返回值。
        async with self._session_factory() as session, session.begin():
            internal_id = await self._lock_user(session, user_id)
            await self._delete_learning_data(session, internal_id)

    async def request_deletion(
        self, user_id: str, requested_at: datetime, effective_at: datetime
    ) -> DeletionRequest:
        # 功能:幂等创建账号注销申请并设置七天等待期
        # 参数:
        #     self: 当前账号注销的SQLAlchemyAccountRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     requested_at: 账号注销申请首次提交的时间
        #     effective_at: 账号注销等待期结束并允许执行的时间
        # 返回:账号注销申请及执行状态
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
            await append_event(
                session,
                event_key=f"deletion-request:{request_id}",
                event_type="DELETION_REQUESTED",
                user_id=internal_id,
                occurred_at=requested_at,
            )
            return DeletionRequest(request_id, user_id, requested_at, effective_at, "PENDING")

    async def revoke_deletion(self, user_id: str, now: datetime) -> DeletionRequest:
        # 功能:撤回等待期内的账号注销申请
        # 参数:
        #     self: 当前账号注销的SQLAlchemyAccountRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:账号注销申请及执行状态
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
            await append_event(
                session,
                event_key=f"deletion-withdrawn:{request.id}",
                event_type="DELETION_WITHDRAWN",
                user_id=internal_id,
                occurred_at=now,
            )
            request.status = "REVOKED"
            request.revoked_at = now
            return request

    async def begin_due_deletions(self, now: datetime) -> list[DeletionRequest]:
        # 功能:锁定到期注销申请并启动本地清理与跨域清理事件
        # 参数:
        #     self: 当前账号注销的SQLAlchemyAccountRepository实例
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:账号注销申请及执行状态集合
        async with self._session_factory() as session, session.begin():
            candidates = (
                (
                    await session.execute(
                        text(
                            "SELECT d.public_id AS request_id, u.public_id AS public_user_id "
                            "FROM account_deletion_request d "
                            "JOIN user_account u ON u.id = d.user_id "
                            "WHERE d.status = 'PENDING' AND d.effective_at <= :now "
                            "ORDER BY d.id LIMIT 100"
                        ),
                        {"now": _database_datetime(now)},
                    )
                )
                .mappings()
                .all()
            )
            started: list[DeletionRequest] = []
            for candidate in candidates:
                public_user_id = str(candidate["public_user_id"])
                request_id = str(candidate["request_id"])
                internal_id = await self._lock_user(session, public_user_id)
                row = (
                    (
                        await session.execute(
                            text(
                                "SELECT id, public_id, requested_at, effective_at, revoked_at, "
                                "completed_at, status FROM account_deletion_request "
                                "WHERE public_id = :request_id AND user_id = :user_id "
                                "AND status = 'PENDING' AND effective_at <= :now FOR UPDATE"
                            ),
                            {
                                "request_id": request_id,
                                "user_id": internal_id,
                                "now": _database_datetime(now),
                            },
                        )
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    continue
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
        # 功能:记录跨域清理回执并在全部成功后完成账号注销
        # 参数:
        #     self: 当前账号注销的SQLAlchemyAccountRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     request_id: 账号注销申请的公开标识,用于匹配跨域清理回执
        #     succeeded: 管理端跨域清理是否已确认成功
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:账号注销申请及执行状态
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
            await append_event(
                session,
                event_key=f"deletion-effective:{request_id}",
                event_type="DELETION_EFFECTIVE",
                user_id=internal_id,
                occurred_at=now,
            )
            await session.execute(
                text(
                    "UPDATE analytics_event SET user_id=NULL, event_key=CONCAT('anonymous:',id) "
                    "WHERE user_id=:user_id"
                ),
                {"user_id": internal_id},
            )
            request.status = "DELETED"
            request.completed_at = now
            return request

    async def get_deletion(self, user_id: str, request_id: str) -> DeletionRequest:
        # 功能:查询用户的当前账号注销申请
        # 参数:
        #     self: 当前账号注销的SQLAlchemyAccountRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     request_id: 账号注销申请的公开标识,用于匹配跨域清理回执
        # 返回:账号注销申请及执行状态
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
        # 功能:锁定用户账号行并取得数据库内部主键
        # 参数:
        #     session: 异步数据库会话
        #     public_id: 当前操作所属用户账号的公开标识
        # 返回:已锁定用户账号的数据库内部主键
        internal_id = await session.scalar(
            text("SELECT id FROM user_account WHERE public_id = :public_id FOR UPDATE"),
            {"public_id": public_id},
        )
        if internal_id is None:
            raise AppError("USER_NOT_FOUND", "用户不存在", 404)
        return int(internal_id)

    @staticmethod
    async def _active_request_row(session: AsyncSession, user_id: int) -> RowMapping | None:
        # 功能:锁定并读取用户当前有效的注销申请数据库记录
        # 参数:
        #     session: 异步数据库会话
        #     user_id: 业务数据库中的用户内部数值主键
        # 返回:数据库查询行字段映射;不存在或无候选时返回None
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
        # 功能:删除用户学习进度、收藏、复习与打卡数据
        # 参数:
        #     session: 异步数据库会话
        #     user_id: 业务数据库中的用户内部数值主键
        # 返回:无返回值。
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
        # 功能:清理账号注销涉及的小程序本地数据
        # 参数:
        #     cls: 当前SQLAlchemyAccountRepository类型,调用类级别的记录转换方法
        #     session: 异步数据库会话
        #     user_id: 业务数据库中的用户内部数值主键
        # 返回:无返回值。
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
        # 功能:将数据库查询行转换为账号注销申请及执行状态
        # 参数:
        #     row: 查询返回的账号注销数据库字段映射
        #     user_id: 当前操作所属用户的公开标识
        # 返回:账号注销申请及执行状态
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
