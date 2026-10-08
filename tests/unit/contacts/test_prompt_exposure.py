from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from juya_miniapp_api.infrastructure.security.field_cipher import FieldCipher
from juya_miniapp_api.modules.contacts.repository import InMemoryContactRepository
from juya_miniapp_api.modules.contacts.router import create_contacts_router
from juya_miniapp_api.modules.contacts.service import ContactService


@pytest.mark.asyncio
async def test_contact_prompt_exposure_is_authenticated_and_idempotent() -> None:
    # 功能:验证联系方式引导曝光需要登录且保持幂等
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    repo = InMemoryContactRepository()
    service = ContactService(repo, FieldCipher(b"k" * 32, b"h" * 32))

    async def user() -> str:
        # 功能:提供路由测试的当前用户公开标识
        # 参数:
        #     无形参。
        # 返回:测试使用的用户公开标识
        return "user"

    app = FastAPI()
    # 匿名函数: clock默认时钟在调用时读取当前UTC时间
    # 参数:
    #     无形参。
    # 返回: 带UTC时区的当前时间
    app.include_router(
        create_contacts_router(service, user_dependency=user, clock=lambda: datetime.now(UTC))
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        one = await client.post(
            "/api/v1/me/contact/prompt-exposures", headers={"Idempotency-Key": "prompt-1"}
        )
        two = await client.post(
            "/api/v1/me/contact/prompt-exposures", headers={"Idempotency-Key": "prompt-1"}
        )
    assert one.status_code == 200
    assert one.json() == {"created": True}
    assert two.json() == {"created": False}
