from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from starlette.requests import Request

from juya_miniapp_api.infrastructure.security.service_hmac import (
    INTERNAL_NONCE_TTL_SECONDS,
    ServicePrincipal,
    sign_request,
    verify_request_signature,
)
from juya_miniapp_api.shared.errors import AppError


class MemoryNonceStore:
    def __init__(self) -> None:
        self.seen: set[tuple[str, str]] = set()

    async def use_once(self, service_name: str, nonce: str, ttl_seconds: int) -> bool:
        assert ttl_seconds == INTERNAL_NONCE_TTL_SECONDS
        key = (service_name, nonce)
        if key in self.seen:
            return False
        self.seen.add(key)
        return True


def make_request(body: bytes, signature: str) -> Request:
    delivered = False

    async def receive() -> dict[str, object]:
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/internal/v1/users/search",
            "raw_path": b"/internal/v1/users/search",
            "query_string": b"limit=20",
            "headers": [
                (b"x-juya-service", b"admin-api"),
                (b"x-juya-timestamp", b"1790553600"),
                (b"x-juya-nonce", b"nonce-123"),
                (b"x-juya-signature", signature.encode()),
            ],
            "client": ("10.0.0.2", 12345),
            "server": ("miniapp.internal", 443),
        },
        receive,
    )


def test_signature_golden_vectors_include_empty_body_and_query() -> None:
    assert (
        sign_request(
            "POST",
            "/internal/v1/users/search?limit=20",
            1_790_553_600,
            "nonce-123",
            b'{"query":"JUYA-1"}',
            b"test-secret",
        )
        == "e63fa6ef766cf0a563147cbc60a7edc53130c8837c4397d7d7f34c1a3170c231"
    )
    assert (
        sign_request(
            "GET",
            "/internal/v1/health",
            1_790_553_600,
            "nonce-empty",
            b"",
            b"test-secret",
        )
        == "3cf9c6bbab6999a0d24d581b3f2272f4efa2cb3d5e831b9630be1ec0c38e820d"
    )


@pytest.mark.asyncio
async def test_verify_rejects_expired_and_replayed_requests() -> None:
    body = b"{}"
    signature = sign_request(
        "POST",
        "/internal/v1/users/search?limit=20",
        1_790_553_600,
        "nonce-123",
        body,
        b"test-secret",
    )
    now = datetime.fromtimestamp(1_790_553_600, UTC)
    store = MemoryNonceStore()

    principal = await verify_request_signature(
        make_request(body, signature), SecretStr("test-secret"), store, now
    )
    assert principal == ServicePrincipal("admin-api")

    with pytest.raises(AppError) as replay:
        await verify_request_signature(
            make_request(body, signature), SecretStr("test-secret"), store, now
        )
    assert replay.value.code == "INTERNAL_REQUEST_REPLAYED"

    with pytest.raises(AppError) as expired:
        await verify_request_signature(
            make_request(body, signature),
            SecretStr("test-secret"),
            MemoryNonceStore(),
            now + timedelta(seconds=301),
        )
    assert expired.value.code == "INTERNAL_SIGNATURE_EXPIRED"
