import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory
from juya_miniapp_api.modules.accounts.repository import SQLAlchemyAccountRepository
from juya_miniapp_api.modules.accounts.service import AccountLifecycleService
from juya_miniapp_api.shared.errors import AppError
from juya_miniapp_api.shared.ids import new_ulid

NOW = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)


def _database_url() -> str:
    # 功能:在测试中读取集成测试的隔离数据库连接配置
    # 参数:
    #     无形参。
    # 返回:集成测试数据库连接地址
    url = os.environ.get("JUYA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("JUYA_TEST_DATABASE_URL is required for MySQL integration tests")
    return url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)


async def _noop_revoke(user_id: str, reason: str, now: datetime) -> None:
    # 功能:在测试中提供不访问真实会话服务的注销测试撤销桩
    # 参数:
    #     user_id: 当前操作所属用户的公开标识
    #     reason: 撤销、纠错或反馈异议的业务原因说明
    #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
    # 返回:无返回值。
    del user_id, reason, now


@asynccontextmanager
async def _owned_account() -> AsyncIterator[
    tuple[SQLAlchemyAccountRepository, async_sessionmaker[AsyncSession], str]
]:
    # 功能:创建和清理隔离数据库中的本用例账号,连接使用北京时间会话
    # 参数:无形参。
    # 返回:仓库、会话工厂和自有用户标识
    engine = create_engine(_database_url())

    @event.listens_for(engine.sync_engine, "connect")
    def set_session_timezone(connection: Any, record: Any) -> None:
        # 功能:让每个测试连接使用非UTC会话时区,验证UTC DATETIME契约
        # 参数:connection: 当前DBAPI连接;record: 连接池记录。
        # 返回:无返回值。
        del record
        cursor = connection.cursor()
        try:
            cursor.execute("SET time_zone = '+08:00'")
        finally:
            cursor.close()

    factory = create_session_factory(engine)
    repository = SQLAlchemyAccountRepository(factory)
    public_id = new_ulid(NOW)
    try:
        async with factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO user_account (public_id, juya_number, status) "
                    "VALUES (:public_id, :juya_number, 'ACTIVE')"
                ),
                {"public_id": public_id, "juya_number": f"JY{public_id[-12:]}"},
            )
        yield repository, factory, public_id
    finally:
        async with factory() as session, session.begin():
            await session.execute(
                text(
                    "DELETE o FROM miniapp_outbox o "
                    "JOIN account_deletion_request d ON d.public_id=o.aggregate_id "
                    "JOIN user_account u ON u.id=d.user_id WHERE u.public_id=:public_id"
                ),
                {"public_id": public_id},
            )
            await session.execute(
                text(
                    "DELETE e FROM analytics_event e "
                    "JOIN user_account u ON u.id=e.user_id WHERE u.public_id=:public_id"
                ),
                {"public_id": public_id},
            )
            await session.execute(
                text(
                    "DELETE d FROM account_deletion_request d "
                    "JOIN user_account u ON u.id=d.user_id WHERE u.public_id=:public_id"
                ),
                {"public_id": public_id},
            )
            await session.execute(
                text("DELETE FROM user_account WHERE public_id = :public_id"),
                {"public_id": public_id},
            )
        await engine.dispose()


async def _account_state(
    factory: async_sessionmaker[AsyncSession], public_id: str
) -> dict[str, object]:
    # 功能:读取申请、账号、审计和发件箱的真实SQL状态用于副作用断言
    # 参数:factory: SQL会话工厂;public_id: 本用例创建的用户标识。
    # 返回:账号与申请完整行、审计事件和清理事件
    async with factory() as session:
        assert await session.scalar(text("SELECT @@session.time_zone")) == "+08:00"
        account = (
            (
                await session.execute(
                    text("SELECT * FROM user_account WHERE public_id=:public_id"),
                    {"public_id": public_id},
                )
            )
            .mappings()
            .one()
        )
        parameters = {"user_id": account["id"]}
        request = (
            (
                await session.execute(
                    text("SELECT * FROM account_deletion_request WHERE user_id=:user_id"),
                    parameters,
                )
            )
            .mappings()
            .one()
        )
        events = (
            (
                await session.execute(
                    text("SELECT * FROM analytics_event WHERE user_id=:user_id ORDER BY id"),
                    parameters,
                )
            )
            .mappings()
            .all()
        )
        outbox = (
            (
                await session.execute(
                    text("SELECT * FROM miniapp_outbox WHERE aggregate_id=:request_id ORDER BY id"),
                    {"request_id": request["public_id"]},
                )
            )
            .mappings()
            .all()
        )
        return {
            "account": dict(account),
            "request": dict(request),
            "events": [dict(row) for row in events],
            "outbox": [dict(row) for row in outbox],
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "now",
    [
        NOW + timedelta(days=7),
        NOW + timedelta(days=7, microseconds=1),
        NOW + timedelta(days=8),
        (NOW + timedelta(days=7)).astimezone(timezone(timedelta(hours=8))),
        (NOW + timedelta(days=7)).replace(tzinfo=None),
    ],
    ids=["exact-cutoff", "after-cutoff", "worker-delayed-day-eight", "beijing", "naive-utc"],
)
async def test_sql_revoke_at_or_after_cutoff_has_no_state_or_audit_changes(now: datetime) -> None:
    # 功能:验证worker未启动时SQL到期撤回被拒且事务不写入任何撤回副作用
    # 参数:now: 截止或截止后的操作时间,包含UTC、北京及无时区UTC表示。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    async with _owned_account() as (repository, factory, public_id):
        service = AccountLifecycleService(repository, _noop_revoke)
        request = await service.request_deletion(public_id, NOW)
        before = await _account_state(factory, public_id)
        assert request.effective_at == NOW + timedelta(days=7)
        assert len(before["events"]) == 1  # type: ignore[arg-type]

        with pytest.raises(AppError) as expired:
            await service.revoke_deletion(public_id, now)

        assert expired.value.code == "DELETION_REVOCATION_EXPIRED"
        assert expired.value.status_code == 409
        assert await _account_state(factory, public_id) == before
        current = await repository.get_deletion(public_id, request.id)
        assert current.status == "PENDING"
        assert current.revoked_at is None
        assert current.completed_at is None


@pytest.mark.asyncio
async def test_sql_revoke_just_before_cutoff_records_one_withdrawal() -> None:
    # 功能:验证截止前一微秒允许SQL撤回,保留原时间且只记录一次撤回事件
    # 参数:无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    async with _owned_account() as (repository, factory, public_id):
        service = AccountLifecycleService(repository, _noop_revoke)
        request = await service.request_deletion(public_id, NOW)
        now = (NOW + timedelta(days=7, microseconds=-1)).astimezone(timezone(timedelta(hours=8)))

        revoked = await service.revoke_deletion(public_id, now)

        current = await repository.get_deletion(public_id, request.id)
        assert revoked.status == current.status == "REVOKED"
        assert current.requested_at == NOW
        assert current.effective_at == NOW + timedelta(days=7)
        assert current.revoked_at == now
        async with factory() as session:
            assert (
                await session.scalar(
                    text("SELECT status FROM user_account WHERE public_id=:public_id"),
                    {"public_id": public_id},
                )
                == "ACTIVE"
            )
            assert (
                await session.scalar(
                    text("SELECT COUNT(*) FROM analytics_event WHERE event_key=:key"),
                    {"key": f"deletion-withdrawn:{request.id}"},
                )
                == 1
            )


@pytest.mark.asyncio
async def test_sql_revoke_uses_persisted_effective_at() -> None:
    # 功能:验证截止守卫读取锁内SQL保存的生效时间,不重算七天或使用旧对象
    # 参数:无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    async with _owned_account() as (repository, factory, public_id):
        service = AccountLifecycleService(repository, _noop_revoke)
        request = await service.request_deletion(public_id, NOW)
        async with factory() as session, session.begin():
            await session.execute(
                text("UPDATE account_deletion_request SET effective_at=:at WHERE public_id=:id"),
                {"at": (NOW + timedelta(days=1)).replace(tzinfo=None), "id": request.id},
            )
        before = await _account_state(factory, public_id)

        with pytest.raises(AppError) as expired:
            await service.revoke_deletion(public_id, NOW + timedelta(days=1))

        assert expired.value.code == "DELETION_REVOCATION_EXPIRED"
        assert await _account_state(factory, public_id) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("execute_first", [False, True], ids=["revoke-first", "execute-first"])
async def test_deletion_revoke_and_execute_are_mutually_exclusive(execute_first: bool) -> None:
    # 功能:验证到期并发无论调用顺序都只进入清理态,不恢复ACTIVE或写撤回事件
    # 参数:execute_first: 是否先调度清理任务。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    async with _owned_account() as (repository, factory, public_id):
        service = AccountLifecycleService(repository, _noop_revoke)
        request = await service.request_deletion(public_id, NOW - timedelta(days=7))
        operations = [service.revoke_deletion(public_id, NOW), service.execute_due_deletions(NOW)]
        if execute_first:
            operations.reverse()
        results = await asyncio.gather(*operations, return_exceptions=True)
        revoke_result, execute_result = results[::-1] if execute_first else results

        assert isinstance(revoke_result, AppError)
        assert revoke_result.code in {"DELETION_REVOCATION_EXPIRED", "DELETION_ALREADY_PROCESSING"}
        current = await repository.get_deletion(public_id, request.id)
        assert current.status == "DELETING"
        assert current.revoked_at is None
        assert execute_result == [current]
        async with factory() as session:
            assert (
                await session.scalar(
                    text("SELECT status FROM user_account WHERE public_id=:public_id"),
                    {"public_id": public_id},
                )
                == "DELETING"
            )
            assert (
                await session.scalar(
                    text("SELECT COUNT(*) FROM miniapp_outbox WHERE aggregate_id=:id"),
                    {"id": request.id},
                )
                == 1
            )
            assert (
                await session.scalar(
                    text("SELECT COUNT(*) FROM analytics_event WHERE event_key=:key"),
                    {"key": f"deletion-withdrawn:{request.id}"},
                )
                == 0
            )
