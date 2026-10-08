from datetime import UTC, datetime

import httpx
import pytest

from juya_miniapp_api.integrations.admin_api.client import AdminApiClient

NOW = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_feedback_commands_send_current_user_and_idempotency_key() -> None:
    # 功能:验证反馈命令透传当前用户和幂等键
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        # 功能:模拟HTTP上游响应以验证反馈命令透传当前用户和幂等键
        # 参数:
        #     request: HTTP测试或上游请求对象
        # 返回:上游HTTP响应对象
        requests.append(request)
        return httpx.Response(
            200,
            json={"id": "feedback-1", "status": "PENDING"},
            request=request,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://admin.test"
    ) as http:
        # 匿名函数: clock测试时钟返回固定操作时间以稳定签名与有效期断言
        # 参数:
        #     无形参。
        # 返回: 测试预设的带UTC时区时间
        # 匿名函数: nonce_factory提供可断言的固定内部签名随机数
        # 参数:
        #     无形参。
        # 返回: 固定的nonce-1测试字符串
        client = AdminApiClient(
            http,
            secret=b"service-secret",
            clock=lambda: NOW,
            nonce_factory=lambda: "nonce-1",
        )
        await client.create_feedback(
            user_id="user-1",
            category="CONTENT",
            description="翻译有误",
            source={"scene_id": "scene-1"},
            screenshots=["feedback/user-1/image.png"],
            idempotency_key="command-1",
        )

    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == "/internal/v1/feedback"
    assert request.headers["X-Idempotency-Key"] == "command-1"
    assert request.headers["X-Juya-Signature"]
    assert request.read().decode() == (
        '{"user_id":"user-1","category":"CONTENT","description":"翻译有误",'
        '"source":{"scene_id":"scene-1"},"screenshots":["feedback/user-1/image.png"]}'
    )


@pytest.mark.asyncio
async def test_feedback_upstream_error_is_mapped_without_internal_details() -> None:
    # 功能:验证反馈上游错误转换后不暴露内部错误细节
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    async def handler(request: httpx.Request) -> httpx.Response:
        # 功能:模拟HTTP上游响应以验证反馈上游错误转换后不暴露内部错误细节
        # 参数:
        #     request: HTTP测试或上游请求对象
        # 返回:上游HTTP响应对象
        return httpx.Response(
            500,
            text="database password leaked",
            request=request,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://admin.test"
    ) as http:
        client = AdminApiClient(http, secret=b"service-secret")
        with pytest.raises(Exception) as error:
            await client.create_feedback(
                user_id="user-1",
                category="OTHER",
                description="问题描述",
                source={},
                screenshots=[],
                idempotency_key="command-1",
            )

    assert getattr(error.value, "code", None) == "ADMIN_API_UNAVAILABLE"
    assert "password" not in str(error.value)
