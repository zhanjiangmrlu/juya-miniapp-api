from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI

from juya_miniapp_api.api.internal.v1.users import create_internal_users_router
from juya_miniapp_api.infrastructure.security.field_cipher import FieldCipher
from juya_miniapp_api.infrastructure.security.service_hmac import ServicePrincipal
from juya_miniapp_api.modules.contacts.repository import InMemoryContactRepository
from juya_miniapp_api.modules.contacts.service import ContactService
from juya_miniapp_api.modules.users.repository import InMemoryUserRepository
from juya_miniapp_api.modules.users.service import UserService
from juya_miniapp_api.shared.errors import install_error_handlers

NOW = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
USER_ID = "01K00000000000000000000001"


@pytest.mark.asyncio
async def test_admin_search_and_detail_expose_contact_only_on_no_store_internal_api() -> None:
    users = InMemoryUserRepository()
    users.add(USER_ID, "JY000000000001", nickname="学习者")
    contacts = ContactService(
        InMemoryContactRepository(),
        FieldCipher(b"k" * 32, b"h" * 32),
    )
    await contacts.save(USER_ID, "admin-visible-id", "privacy-v1", "PROFILE", NOW)
    user_service = UserService(users, contacts)

    async def admin_service() -> ServicePrincipal:
        return ServicePrincipal("juya-admin-api")

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        create_internal_users_router(user_service, contacts, service_dependency=admin_service)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        search = await client.post(
            "/internal/v1/users/search",
            json={"wechat_id": " ADMIN-VISIBLE-ID "},
            headers={"X-Admin-Id": "admin-1"},
        )
        detail = await client.get(
            f"/internal/v1/users/{USER_ID}", headers={"X-Admin-Id": "admin-1"}
        )
        query_string_search = await client.get(
            "/internal/v1/users/search?wechat_id=admin-visible-id"
        )

    assert search.status_code == 200
    assert search.headers["Cache-Control"] == "no-store"
    assert search.json()["items"][0]["public_id"] == USER_ID
    assert search.json()["items"][0]["wechat_id"] == "admin-visible-id"
    assert detail.status_code == 200
    assert detail.headers["Cache-Control"] == "no-store"
    assert detail.json()["contact"]["wechat_id"] == "admin-visible-id"
    serialized = search.text + detail.text
    assert "openid" not in serialized.lower()
    assert "refresh_token" not in serialized.lower()
    assert query_string_search.status_code == 405


@pytest.mark.asyncio
async def test_admin_correction_routes_require_headers_and_never_leak_secret_fields() -> None:
    users = InMemoryUserRepository()
    users.add(USER_ID, "JY000000000001", nickname="学习者")
    repository = InMemoryContactRepository()
    repository.admin_identities[USER_ID] = ("JY000000000001", "学习者")
    contacts = ContactService(
        repository,
        FieldCipher(b"k" * 32, b"h" * 32),
    )
    await contacts.save(USER_ID, "admin-visible-id", "privacy-v1", "PROFILE", NOW)
    correction = await contacts.request_correction(USER_ID, "微信号需要更正", NOW)
    user_service = UserService(users, contacts)

    async def admin_service() -> ServicePrincipal:
        return ServicePrincipal("juya-admin-api")

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        create_internal_users_router(user_service, contacts, service_dependency=admin_service)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        missing_admin = await client.post(
            "/internal/v1/contact-corrections/search",
            json={"status": "PENDING", "page": 1, "page_size": 20},
        )
        search = await client.post(
            "/internal/v1/contact-corrections/search",
            json={"status": "PENDING", "page": 1, "page_size": 20},
            headers={"X-Admin-Id": "admin-1"},
        )
        detail = await client.get(
            f"/internal/v1/contact-corrections/{correction.public_id}",
            headers={"X-Admin-Id": "admin-1"},
        )
        missing_key = await client.post(
            f"/internal/v1/contact-corrections/{correction.public_id}/decision",
            json={"decision": "APPROVED"},
            headers={"X-Admin-Id": "admin-1"},
        )
        decision = await client.post(
            f"/internal/v1/contact-corrections/{correction.public_id}/decision",
            json={"decision": "APPROVED"},
            headers={
                "X-Admin-Id": "admin-1",
                "X-Idempotency-Key": "decision-key-1",
            },
        )
        replay = await client.post(
            f"/internal/v1/contact-corrections/{correction.public_id}/decision",
            json={"decision": "APPROVED"},
            headers={
                "X-Admin-Id": "admin-1",
                "X-Idempotency-Key": "decision-key-1",
            },
        )

    assert missing_admin.status_code == 422
    assert search.status_code == 200
    assert search.headers["Cache-Control"] == "no-store"
    assert search.json()["total"] == 1
    assert search.json()["items"][0]["wechat_id"] == "admin-visible-id"
    assert detail.status_code == 200
    assert detail.headers["Cache-Control"] == "no-store"
    assert detail.json()["reason"] == "微信号需要更正"
    assert detail.json()["timeline"][-1]["event_type"] == "CONTACT_CORRECTION_CREATED"
    assert missing_key.status_code == 422
    assert decision.status_code == 200
    assert decision.headers["Cache-Control"] == "no-store"
    assert replay.status_code == 200
    assert replay.json() == decision.json()
    serialized = search.text + detail.text + decision.text
    assert "new_wechat" not in serialized.lower()
    assert "openid" not in serialized.lower()
    assert "ciphertext" not in serialized.lower()
    assert "hmac" not in serialized.lower()
