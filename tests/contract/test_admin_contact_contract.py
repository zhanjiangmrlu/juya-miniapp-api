from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI

from juya_miniapp_api.api.internal.v1.users import create_internal_users_router
from juya_miniapp_api.infrastructure.security.field_cipher import FieldCipher
from juya_miniapp_api.infrastructure.security.service_hmac import ServicePrincipal
from juya_miniapp_api.modules.contacts.repository import InMemoryContactRepository
from juya_miniapp_api.modules.contacts.service import ContactService
from juya_miniapp_api.modules.learning.admin_projection import InMemoryLearningOverviewRepository
from juya_miniapp_api.modules.users.repository import InMemoryUserRepository
from juya_miniapp_api.modules.users.service import UserService
from juya_miniapp_api.shared.errors import install_error_handlers

NOW = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
USER_ID = "01K00000000000000000000001"


@pytest.mark.asyncio
async def test_admin_search_and_detail_expose_contact_only_on_no_store_internal_api() -> None:
    # 功能:验证管理端内部用户检索和详情仅在禁止缓存的接口返回联系方式
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    users = InMemoryUserRepository()
    users.add(USER_ID, "JY000000000001", nickname="学习者")
    contacts = ContactService(
        InMemoryContactRepository(),
        FieldCipher(b"k" * 32, b"h" * 32),
    )
    await contacts.save(USER_ID, "admin-visible-id", "privacy-v1", "PROFILE", NOW)
    user_service = UserService(users, contacts)

    async def admin_service() -> ServicePrincipal:
        # 功能:提供内部接口测试的管理端服务身份
        # 参数:
        #     无形参。
        # 返回:已验证的内部调用服务身份
        return ServicePrincipal("juya-admin-api")

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        create_internal_users_router(
            user_service,
            contacts,
            InMemoryLearningOverviewRepository(),
            service_dependency=admin_service,
        )
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
    # 功能:验证管理端纠错接口校验必要请求头且不暴露密文字段
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
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
        # 功能:提供内部接口测试的管理端服务身份
        # 参数:
        #     无形参。
        # 返回:已验证的内部调用服务身份
        return ServicePrincipal("juya-admin-api")

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        create_internal_users_router(
            user_service,
            contacts,
            InMemoryLearningOverviewRepository(),
            service_dependency=admin_service,
        )
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


@pytest.mark.asyncio
async def test_admin_batch_contact_projection_preserves_order_and_learning_returns_counts() -> None:
    # 功能:验证批量联系方式投影保持请求顺序且学习投影返回数量
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    second_user_id = "01K00000000000000000000002"
    users = InMemoryUserRepository()
    users.add(USER_ID, "JY000000000001", nickname="学习者一")
    users.add(second_user_id, "JY000000000002", nickname="学习者二")
    contacts = ContactService(
        InMemoryContactRepository(),
        FieldCipher(b"k" * 32, b"h" * 32),
    )
    await contacts.save(USER_ID, "first-contact", "privacy-v1", "PROFILE", NOW)
    await contacts.save(second_user_id, "second-contact", "privacy-v1", "PROFILE", NOW)
    overviews = InMemoryLearningOverviewRepository()
    overviews.open_scene_completion_events.extend(
        [(USER_ID, "scene-1"), (USER_ID, "scene-1"), (USER_ID, "scene-2")]
    )
    overviews.checkins.extend([(USER_ID, NOW.date()), (USER_ID, NOW.date())])
    overviews.favorite_entries.extend([(USER_ID, "favorite-1"), (USER_ID, "favorite-2")])

    async def admin_service() -> ServicePrincipal:
        # 功能:提供内部接口测试的管理端服务身份
        # 参数:
        #     无形参。
        # 返回:已验证的内部调用服务身份
        return ServicePrincipal("juya-admin-api")

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        create_internal_users_router(
            UserService(users, contacts),
            contacts,
            overviews,
            service_dependency=admin_service,
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        projections = await client.post(
            "/internal/v1/users/contact-projections",
            json={"user_ids": [second_user_id, "missing-user", USER_ID]},
            headers={"X-Admin-Id": "admin-1"},
        )
        overview = await client.get(
            f"/internal/v1/users/{USER_ID}/learning-overview",
            headers={"X-Admin-Id": "admin-1"},
        )
        oversized = await client.post(
            "/internal/v1/users/contact-projections",
            json={"user_ids": [f"user-{index}" for index in range(101)]},
            headers={"X-Admin-Id": "admin-1"},
        )

    assert projections.status_code == 200
    assert projections.headers["Cache-Control"] == "no-store"
    assert [item["user_id"] for item in projections.json()["contacts"]] == [
        second_user_id,
        USER_ID,
    ]
    assert projections.json()["contacts"][0] == {
        "user_id": second_user_id,
        "wechat_id": "second-contact",
        "contact_status": "PENDING",
        "change_pending": False,
        "verified_at": None,
        "verified_by": None,
        "updated_at": NOW.isoformat().replace("+00:00", "Z"),
    }
    assert overview.status_code == 200
    assert overview.headers["Cache-Control"] == "no-store"
    assert overview.json() == {
        "open_scene_completed_count": 2,
        "learning_days": 1,
        "favorite_count": 2,
    }
    assert oversized.status_code == 422
