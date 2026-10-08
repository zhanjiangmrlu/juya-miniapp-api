import asyncio
import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from juya_miniapp_api.infrastructure.db.session import (
    create_engine,
    create_session_factory,
)
from juya_miniapp_api.infrastructure.security.field_cipher import FieldCipher
from juya_miniapp_api.infrastructure.security.jwt_service import JwtService
from juya_miniapp_api.integrations.wechat.protocol import WechatIdentity
from juya_miniapp_api.modules.auth.repository import SQLAlchemyAuthRepository
from juya_miniapp_api.modules.auth.service import SessionService

NOW = datetime(2026, 9, 28, 18, 0, tzinfo=UTC)


class SameIdentityWechatProvider:
    async def exchange_code(self, code: str) -> WechatIdentity:
        # 功能:在测试中使用微信临时登录码换取应用身份与openid
        # 参数:
        #     self: 当前小程序的SameIdentityWechatProvider实例
        #     code: 微信客户端取得的一次性登录码
        # 返回:微信应用标识与openid身份
        del code
        return WechatIdentity(app_id="wx-concurrency-test", openid="same-openid")


def _database_url() -> str:
    # 功能:在测试中读取集成测试的隔离数据库连接配置
    # 参数:
    #     无形参。
    # 返回:集成测试数据库连接地址
    url = os.environ.get("JUYA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("JUYA_TEST_DATABASE_URL is required for MySQL integration tests")
    return url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)


@pytest.mark.asyncio
async def test_same_openid_concurrent_login_creates_one_user_and_identity() -> None:
    # 功能:验证同一openid并发登录只创建一个账号与身份绑定
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    engine = create_engine(_database_url())
    factory = create_session_factory(engine)
    try:
        async with factory() as session, session.begin():
            await session.execute(
                text(
                    "DELETE s FROM user_session s "
                    "JOIN user_account u ON u.id = s.user_id "
                    "JOIN user_app_identity i ON i.user_id = u.id "
                    "WHERE i.app_id = 'wx-concurrency-test'"
                )
            )
            await session.execute(
                text(
                    "DELETE p FROM user_profile p "
                    "JOIN user_account u ON u.id = p.user_id "
                    "JOIN user_app_identity i ON i.user_id = u.id "
                    "WHERE i.app_id = 'wx-concurrency-test'"
                )
            )
            await session.execute(
                text("DELETE FROM user_app_identity WHERE app_id = 'wx-concurrency-test'")
            )
            await session.execute(
                text(
                    "DELETE u FROM user_account u "
                    "LEFT JOIN user_app_identity i ON i.user_id = u.id "
                    "WHERE u.juya_number LIKE 'JY%' AND i.id IS NULL"
                )
            )

        service = SessionService(
            SameIdentityWechatProvider(),
            SQLAlchemyAuthRepository(factory),
            FieldCipher(b"k" * 32, b"h" * 32),
            JwtService(b"j" * 32, kid="miniapp-key-1"),
        )

        sessions = await asyncio.gather(
            *(service.login_with_wechat(f"code-{index}", "test-device", NOW) for index in range(20))
        )

        assert len({item.user.public_id for item in sessions}) == 1
        assert len({item.user.juya_number for item in sessions}) == 1
        async with factory() as session:
            user_count = await session.scalar(
                text(
                    "SELECT COUNT(*) FROM user_account u "
                    "JOIN user_app_identity i ON i.user_id = u.id "
                    "WHERE i.app_id = 'wx-concurrency-test'"
                )
            )
            identity_count = await session.scalar(
                text("SELECT COUNT(*) FROM user_app_identity WHERE app_id = 'wx-concurrency-test'")
            )
            session_count = await session.scalar(
                text(
                    "SELECT COUNT(*) FROM user_session s "
                    "JOIN user_app_identity i ON i.user_id = s.user_id "
                    "WHERE i.app_id = 'wx-concurrency-test'"
                )
            )
        assert user_count == 1
        assert identity_count == 1
        assert session_count == 20
    finally:
        await engine.dispose()
