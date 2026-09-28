from datetime import UTC, datetime

import httpx
import pytest

from juya_miniapp_api.integrations.admin_api.client import AdminApiClient

NOW = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_feedback_commands_send_current_user_and_idempotency_key() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"id": "feedback-1", "status": "PENDING"},
            request=request,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://admin.test"
    ) as http:
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
    async def handler(request: httpx.Request) -> httpx.Response:
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
