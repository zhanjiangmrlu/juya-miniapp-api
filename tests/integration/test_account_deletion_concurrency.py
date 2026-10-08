import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory
from juya_miniapp_api.modules.accounts.repository import SQLAlchemyAccountRepository
from juya_miniapp_api.modules.accounts.service import AccountLifecycleService
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


@pytest.mark.asyncio
async def test_deletion_revoke_and_execute_are_mutually_exclusive() -> None:
    # 功能:验证并发撤回与执行注销只产生一个最终状态
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    engine = create_engine(_database_url())
    factory = create_session_factory(engine)
    repository = SQLAlchemyAccountRepository(factory)
    service = AccountLifecycleService(repository, _noop_revoke)
    public_id = new_ulid(NOW)
    request_id: str | None = None
    try:
        async with factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO user_account (public_id, juya_number, status) "
                    "VALUES (:public_id, :juya_number, 'ACTIVE')"
                ),
                {"public_id": public_id, "juya_number": f"JY{public_id[-12:]}"},
            )
        request = await service.request_deletion(public_id, NOW - timedelta(days=7))
        request_id = request.id

        results = await asyncio.gather(
            service.revoke_deletion(public_id, NOW),
            service.execute_due_deletions(NOW),
            return_exceptions=True,
        )

        assert not any(isinstance(item, SQLAlchemyError) for item in results)
        assert any(not isinstance(item, Exception) for item in results)

        current = await repository.get_deletion(public_id, request.id)
        assert current.status in {"REVOKED", "DELETING"}
    finally:
        async with factory() as session, session.begin():
            if request_id is not None:
                await session.execute(
                    text("DELETE FROM miniapp_outbox WHERE aggregate_id = :request_id"),
                    {"request_id": request_id},
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
