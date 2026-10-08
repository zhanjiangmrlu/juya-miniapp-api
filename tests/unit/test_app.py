import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from juya_miniapp_api.infrastructure.config import Settings
from juya_miniapp_api.main import create_app
from juya_miniapp_api.shared.errors import AppError


def make_app() -> FastAPI:
    # 功能:在测试中创建供HTTP测试使用的小程序API应用
    # 参数:
    #     无形参。
    # 返回:FastAPI应用
    return create_app(Settings(environment="test"))


@pytest.mark.asyncio
async def test_live_health_returns_request_id() -> None:
    # 功能:验证存活检查响应包含请求标识
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    app = make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "juya-miniapp-api"}
    assert response.headers["X-Request-ID"]
    assert response.headers["traceparent"].startswith("00-")


@pytest.mark.asyncio
async def test_trace_context_is_propagated() -> None:
    # 功能:验证请求链路追踪上下文正确透传
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    app = make_app()
    traceparent = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/live", headers={"traceparent": traceparent})

    assert response.headers["traceparent"] == traceparent


@pytest.mark.asyncio
async def test_ready_rejects_old_schema() -> None:
    # 功能:验证过旧数据库结构导致就绪检查失败
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    async def old_schema() -> dict[str, bool]:
        # 功能:在测试中返回过旧迁移版本的就绪探针测试结果
        # 参数:
        #     无形参。
        # 返回:表示数据库版本不满足要求的就绪状态映射
        return {"mysql": True, "schema": False}

    app = create_app(Settings(environment="test"), readiness_probe=old_schema)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/ready", headers={"X-Request-ID": "ready-123"})

    assert response.status_code == 503
    assert response.json() == {
        "code": "SERVICE_NOT_READY",
        "message": "服务尚未就绪",
        "request_id": "ready-123",
        "details": {"checks": {"mysql": True, "schema": False}},
    }


@pytest.mark.asyncio
async def test_app_error_uses_safe_shape() -> None:
    # 功能:验证业务异常响应只包含安全字段
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    app = make_app()

    @app.get("/boom")
    async def boom() -> None:
        # 功能:在测试中主动抛出业务异常以验证安全错误响应
        # 参数:
        #     无形参。
        # 返回:无返回值。
        raise AppError("STATE_CONFLICT", "当前状态不允许此操作", 409, {"state": "ENDED"})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/boom", headers={"X-Request-ID": "request-123"})

    assert response.status_code == 409
    assert response.json() == {
        "code": "STATE_CONFLICT",
        "message": "当前状态不允许此操作",
        "request_id": "request-123",
        "details": {"state": "ENDED"},
    }


@pytest.mark.asyncio
async def test_validation_error_does_not_expose_input() -> None:
    # 功能:验证请求校验失败响应不暴露原始输入
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    app = make_app()

    class Payload(BaseModel):
        count: int

    @app.post("/validated")
    async def validated(payload: Payload) -> Payload:
        # 功能:在测试中返回已通过请求模型校验的测试载荷
        # 参数:
        #     payload: 已校验的请求模型校验测试的字段
        # 返回:通过请求字段校验的Payload测试模型
        return payload

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/validated",
            headers={"X-Request-ID": "request-456"},
            json={"count": "secret-value"},
        )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert "secret-value" not in response.text
