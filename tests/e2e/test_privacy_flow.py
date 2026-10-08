from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from juya_miniapp_api.modules.accounts.repository import InMemoryAccountRepository
from juya_miniapp_api.modules.accounts.router import create_accounts_router
from juya_miniapp_api.modules.accounts.service import AccountLifecycleService
from juya_miniapp_api.shared.errors import install_error_handlers


@pytest.mark.asyncio
async def test_clear_then_request_and_revoke_deletion_over_http() -> None:
    # 功能:验证通过HTTP清空学习数据并申请和撤回账号注销
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    repository = InMemoryAccountRepository()
    repository.seed_user("user-1")
    repository.learning_rows.add("user-1")

    async def current_user() -> str:
        # 功能:校验访问凭证与会话状态并取得当前用户公开标识
        # 参数:
        #     无形参。
        # 返回:通过鉴权的用户公开标识
        return "user-1"

    async def revoke_sessions(user_id: str, reason: str, now: datetime) -> None:
        # 功能:在测试中记录注销流程中的会话撤销调用供断言
        # 参数:
        #     user_id: 当前操作所属用户的公开标识
        #     reason: 撤销、纠错或反馈异议的业务原因说明
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:无返回值。
        del user_id, reason, now

    now = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)
    service = AccountLifecycleService(repository, revoke_sessions)
    app = FastAPI()
    install_error_handlers(app)
    # 匿名函数: clock测试时钟返回固定操作时间以稳定签名与有效期断言
    # 参数:
    #     无形参。
    # 返回: 测试预设的带UTC时区时间
    app.include_router(
        create_accounts_router(service, user_dependency=current_user, clock=lambda: now)
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        cleared = await client.request(
            "DELETE",
            "/api/v1/me/learning-data",
            json={"confirmation": "CLEAR_LEARNING_DATA"},
        )
        requested = await client.post("/api/v1/me/deletion")
        revoked = await client.post("/api/v1/me/deletion/revoke")

    assert cleared.status_code == 204
    assert "user-1" not in repository.learning_rows
    assert requested.status_code == 202
    assert requested.json()["status"] == "PENDING"
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "REVOKED"
