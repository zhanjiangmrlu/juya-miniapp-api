import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlsplit

import alibabacloud_oss_v2 as oss
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory
from juya_miniapp_api.integrations.oss.avatar import AvatarStore
from juya_miniapp_api.modules.contacts.service import ContactService
from juya_miniapp_api.modules.users.repository import SQLAlchemyUserRepository
from juya_miniapp_api.modules.users.router import create_users_router
from juya_miniapp_api.modules.users.service import UserService
from juya_miniapp_api.shared.errors import install_error_handlers
from juya_miniapp_api.shared.ids import new_ulid


@pytest.mark.asyncio
async def test_mysql_profile_post_patch_and_me_use_own_fixed_avatar_with_local_sdk_signing():
    url = os.getenv("JUYA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("isolated MySQL required")
    engine = create_engine(url.replace("mysql+pymysql://", "mysql+asyncmy://"))
    sessions = create_session_factory(engine)
    now = datetime.now(UTC)
    owner = new_ulid(now)
    key = f"avatars/{owner}/fixed.png"
    store = AvatarStore(
        region="cn-hangzhou",
        bucket="test-private",
        endpoint=None,
        credentials_provider=oss.credentials.StaticCredentialsProvider(
            "test-access", "test-secret"
        ),
    )
    # Signing uses the real SDK without a network operation; upload byte validation is unit-tested.
    contacts = Mock(spec=ContactService)
    contacts.get = AsyncMock(return_value=None)
    verifier = AsyncMock(return_value=key)
    service = UserService(
        SQLAlchemyUserRepository(sessions),
        contacts,
        avatar_verifier=verifier,
        avatar_url_signer=store.sign_avatar_url,
    )

    async def current_user():
        return owner

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(create_users_router(service, user_dependency=current_user))
    try:
        async with sessions() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO user_account(public_id,juya_number,status) "
                    "VALUES(:owner,:number,'ACTIVE')"
                ),
                {"owner": owner, "number": "JY" + owner[-12:]},
            )
            await session.execute(
                text(
                    "INSERT INTO user_profile(user_id,nickname,source) "
                    "SELECT id,'old','WECHAT' FROM user_account WHERE public_id=:owner"
                ),
                {"owner": owner},
            )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local"
        ) as client:
            assert (await client.get("/api/v1/me")).json()["avatar_url"] is None
            for method in ("POST", "PATCH"):
                response = await client.request(
                    method,
                    "/api/v1/me/profile",
                    json={
                        "nickname": f" {method}昵称 ",
                        "avatar_object_key": f"uploads/avatars/{owner}/new.png",
                    },
                )
                assert response.status_code == 200
                assert response.json()["nickname"] == method + "昵称"
                assert response.json()["avatar_object_key"] == key
                signed = urlsplit(response.json()["avatar_url"])
                assert signed.scheme == "https" and signed.path == "/" + key
                assert parse_qs(signed.query)["x-oss-signature"]
                read = await client.get("/api/v1/me")
                assert read.json()["nickname"] == method + "昵称"
                assert urlsplit(read.json()["avatar_url"]).path == "/" + key
                assert read.headers["cache-control"] == "private, no-store"
                rejected = await client.request(
                    method,
                    "/api/v1/me/profile",
                    json={"nickname": "denied", "avatar_object_key": "avatars/other/fixed.png"},
                )
                assert rejected.status_code == 422
            async with sessions() as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT p.nickname,p.avatar_object_key FROM user_profile p "
                            "JOIN user_account u ON u.id=p.user_id WHERE u.public_id=:owner"
                        ),
                        {"owner": owner},
                    )
                ).one()
                assert row.nickname == "PATCH昵称" and row.avatar_object_key == key
    finally:
        async with sessions() as session, session.begin():
            await session.execute(
                text("DELETE FROM user_account WHERE public_id=:owner"), {"owner": owner}
            )
        await engine.dispose()
