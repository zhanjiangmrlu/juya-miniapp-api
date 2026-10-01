import json
from datetime import UTC, datetime

import httpx
import pytest

from juya_miniapp_api.infrastructure.security.service_hmac import sign_request
from juya_miniapp_api.integrations.admin_api.client import (
    AdminApiClient,
    AdminApiUnavailable,
)

NOW = datetime(2026, 9, 28, 21, 0, tzinfo=UTC)
SECRET = b"s" * 32


@pytest.mark.asyncio
async def test_client_uses_timeouts_hmac_headers_and_typed_access_contract() -> None:
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "scene_id": "scene-1",
                        "level": "FORMAL",
                        "sources": ["FORMAL:grant-1"],
                        "earliest_expires_at": "2026-10-01T00:00:00Z",
                    }
                ]
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://admin-api")
    client = AdminApiClient(
        http,
        secret=SECRET,
        clock=lambda: NOW,
        nonce_factory=lambda: "nonce-1",
    )
    try:
        result = await client.batch_access("user-1", ["scene-1"])
    finally:
        await http.aclose()

    assert result[0].level == "FORMAL"
    request = captured[0]
    body = await request.aread()
    timestamp = int(NOW.timestamp())
    assert request.headers["X-Juya-Service"] == "juya-miniapp-api"
    assert request.headers["X-Juya-Timestamp"] == str(timestamp)
    assert request.headers["X-Juya-Nonce"] == "nonce-1"
    assert request.headers["X-Juya-Signature"] == sign_request(
        "POST", "/internal/v1/access/batch", timestamp, "nonce-1", body, SECRET
    )
    assert json.loads(body) == {"user_id": "user-1", "scene_ids": ["scene-1"]}
    assert client.timeout.connect == 2.0
    assert client.timeout.read == 8.0


@pytest.mark.asyncio
async def test_get_and_idempotent_command_retry_but_plain_post_does_not() -> None:
    attempts: dict[str, int] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        attempts[path] = attempts.get(path, 0) + 1
        if attempts[path] == 1:
            raise httpx.ConnectError("temporary", request=request)
        if path.endswith("/modules"):
            return httpx.Response(200, json={"items": []})
        return httpx.Response(
            200,
            json={
                "access": "OPEN",
                "sources": ["OPEN"],
                "earliest_expires_at": None,
                "activated_at": None,
                "scene": {
                    "scene_id": "scene-1",
                    "revision_id": "rev-1",
                    "content_version": 1,
                    "content": {"title_en": "Scene"},
                },
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://admin-api")
    nonces = iter(("n1", "n2", "n3", "n4", "n5"))
    client = AdminApiClient(
        http, secret=SECRET, clock=lambda: NOW, nonce_factory=lambda: next(nonces)
    )
    try:
        assert await client.get_modules() == []
        opened = await client.open_scene("user-1", "scene-1", "open-1")
        assert opened.access == "OPEN"
        with pytest.raises(AdminApiUnavailable):
            await client.get_catalog("user-1", {})
    finally:
        await http.aclose()

    assert attempts["/internal/v1/learning/modules"] == 2
    assert attempts["/internal/v1/scenes/scene-1/open"] == 2
    assert attempts["/internal/v1/learning/catalog"] == 1
