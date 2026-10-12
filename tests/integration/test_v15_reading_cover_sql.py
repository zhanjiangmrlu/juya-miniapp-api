"""Private SQL/HMAC/current cover signer; external OSS HEAD/signing are fixture ports."""

import hashlib
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import text

from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory
from juya_miniapp_api.integrations.admin_api.v15_client import AdminV15Client
from juya_miniapp_api.modules.short_reading.authority import V15ReadingAuthority
from juya_miniapp_api.shared.errors import AppError
from juya_miniapp_api.shared.ids import new_ulid

ADMIN_SRC = (
    Path(
        os.getenv(
            "JUYA_CI_ADMIN_SOURCE", str(Path(__file__).resolve().parents[3] / "juya-admin-api")
        )
    )
    / "src"
)


@pytest.mark.asyncio
async def test_reading_preview_cover_uses_actual_sql_hmac_binding_without_body_access():
    database = os.getenv("JUYA_TEST_DATABASE_URL")
    redis_url = os.getenv("JUYA_TEST_REDIS_URL")
    if not database or not redis_url:
        pytest.skip("private MySQL/Redis required")
    db = urlsplit(database)
    cache = urlsplit(redis_url)
    assert os.getenv("JUYA_ENVIRONMENT") == "test"
    assert db.hostname == cache.hostname == "127.0.0.1"
    assert re.fullmatch(r"/juya_v13_mini_[0-9a-f]{32}", db.path)
    admin_src = ADMIN_SRC
    sys.path.insert(0, str(admin_src))
    sys.path.append(
        os.getenv("JUYA_CI_ADMIN_SITE_PACKAGES", str(admin_src.parent / ".venv/Lib/site-packages"))
    )
    from juya_admin_api.infrastructure.security.service_hmac import (
        RedisNonceStore,
        verify_request_signature,
    )
    from juya_admin_api.modules.short_reading.store import ShortReadingStore
    from juya_admin_api.modules.v15_access.resources import SQLResourceSigner
    from juya_admin_api.modules.v15_access.router import create_v15_access_router
    from juya_admin_api.modules.v15_access.service import V15AccessService
    from juya_admin_api.modules.v15_access.sql_adapters import (
        SQLAuthorizer,
        SQLContentLoader,
        SQLFavoriteAuthority,
    )
    from juya_admin_api.shared.errors import AppError as AdminError

    engine = create_engine(database.replace("mysql+pymysql://", "mysql+asyncmy://", 1))
    sessions = create_session_factory(engine)
    redis = Redis.from_url(redis_url, decode_responses=True)
    now = datetime.now(UTC)
    user, series, cover = [new_ulid(now) for _ in range(3)]
    key = "sealed/media/images/" + cover + ".png"
    fingerprint = hashlib.sha256(cover.encode()).hexdigest()
    store = ShortReadingStore(sessions)
    secret = b"private-reading-cover-hmac-not-production"
    readings = []
    old_modules = None
    signed_keys = []

    class FixtureOSS:
        async def head_object(self, object_key):
            assert object_key == key
            return SimpleNamespace(size=4, sha256=fingerprint, content_type="image/png")

        async def sign_get_url(self, object_key, ttl):
            assert object_key == key and 1 <= ttl <= 60
            signed_keys.append(object_key)
            return "https://media.invalid/private-cover?fixture=external-port"

    try:
        async with sessions() as session, session.begin():
            old_modules = (
                await session.execute(
                    text("SELECT module_type,enabled FROM learning_module_config")
                )
            ).all()
            await session.execute(
                text(
                    "INSERT INTO learning_module_config "
                    "(id,module_type,display_name,sort_order,enabled) "
                    "VALUES(102,'short_reading','短文',2,1) ON DUPLICATE KEY UPDATE enabled=1"
                )
            )
            await session.execute(
                text(
                    "INSERT INTO user_account(public_id,juya_number,status) VALUES(:id,:n,'ACTIVE')"
                ),
                {"id": user, "n": "JY" + user[-12:]},
            )
            await session.execute(
                text(
                    "INSERT INTO content_series(public_id,slug,title,status) "
                    "VALUES(:id,:id,'private cover fixture','PUBLISHED')"
                ),
                {"id": series},
            )
            await session.execute(
                text(
                    "INSERT INTO media_asset(public_id,object_key,asset_type,content_type,"
                    "size_bytes,sha256,status,security_status,created_by,created_at,width,height) "
                    "VALUES(:id,:key,'images','image/png',4,:hash,'CONFIRMED','PASSED',:user,:now,1,1)"
                ),
                {
                    "id": cover,
                    "key": key,
                    "hash": fingerprint,
                    "user": user,
                    "now": now.replace(tzinfo=None),
                },
            )
            fixture = {
                "title_en": "Safe cover fixture",
                "title_zh": "隔离封面",
                "category": "daily",
                "topic": "coffee",
                "level": "A1",
                "safe_intro": "Safe introduction only",
                "cover_asset_id": cover,
                "paragraphs_en": ["Hidden body must never leak"],
                "paragraph_ids": ["p1"],
                "translation_zh": "仅隔离测试正文",
                "audio": None,
            }
            for position, asset in enumerate((cover, None)):
                created = await store.create_within(
                    session, series, fixture | {"cover_asset_id": asset}, user
                )
                identity, revision = created["content_id"], created["revision_id"]
                readings.append(identity)
                await session.execute(
                    text(
                        "UPDATE short_reading_revision SET status='PUBLISHED' WHERE public_id=:id"
                    ),
                    {"id": revision},
                )
                await session.execute(
                    text(
                        "UPDATE short_reading SET status='PUBLISHED',published_revision_id=:r,"
                        "sort_order=:sort WHERE public_id=:id"
                    ),
                    {"r": revision, "id": identity, "sort": -2147483648 + position},
                )

        signer = SQLResourceSigner(FixtureOSS())
        service = V15AccessService(
            sessions,
            content_loader=SQLContentLoader(store),
            authorizer=SQLAuthorizer(),
            resource_signer=signer,
            credential_secret=secret,
            favorite_authority=SQLFavoriteAuthority(),
        )
        admin = FastAPI()
        nonce_store = RedisNonceStore(redis, key_prefix="v15-cover-test:" + user)

        async def principal(request: Request):
            return await verify_request_signature(
                request, SecretStr(secret.decode()), nonce_store, datetime.now(UTC)
            )

        @admin.exception_handler(AdminError)
        async def error_response(request, error):
            return JSONResponse(
                {"code": error.code, "message": error.message}, status_code=error.status_code
            )

        admin.include_router(create_v15_access_router(service, current_service=principal))
        async with httpx.AsyncClient(
            base_url="http://admin", transport=httpx.ASGITransport(app=admin)
        ) as delegated:
            client = AdminV15Client(delegated, secret=secret)
            authority = V15ReadingAuthority(sessions, client)
            current = await authority.preview(user, readings[0])
            assert current.access == "PREVIEW" and current.cover_url.startswith(
                "https://media.invalid/"
            )
            assert "Hidden body" not in current.model_dump_json()
            assert not {
                "paragraphs_en",
                "translation_zh",
                "whole_audio",
                "cover_asset_id",
                "resource_version",
            }.intersection(current.model_dump())
            missing = await authority.preview(user, readings[1])
            assert missing.cover_url is None and signed_keys == [key]
            page = await authority.listing(user, None, 2)
            listed = next(row for row in page["items"] if row["content_id"] == readings[0])
            assert listed["cover_url"] == current.cover_url and listed["access"] == "PREVIEW"
            with pytest.raises(AppError) as full:
                await authority.open_reading(user, readings[0])
            assert full.value.status_code == 403
            with pytest.raises(AppError) as full_resource:
                await client.resource(user, "short_reading", readings[0], cover, None)
            assert full_resource.value.status_code == 403

            # Flip only after admin signing's transaction has returned: the second
            # delegated preview must reject an address produced before module closure.
            class ClosingClient(AdminV15Client):
                async def preview_cover(self, *args):
                    result = await super().preview_cover(*args)
                    async with sessions() as session, session.begin():
                        await session.execute(
                            text(
                                "UPDATE learning_module_config SET enabled=0 "
                                "WHERE module_type='short_reading'"
                            )
                        )
                    return result

            with pytest.raises(AppError) as changed:
                await V15ReadingAuthority(
                    sessions, ClosingClient(delegated, secret=secret)
                ).preview(user, readings[0])
            assert changed.value.status_code == 403
            async with sessions() as session, session.begin():
                await session.execute(
                    text(
                        "UPDATE learning_module_config SET enabled=1 "
                        "WHERE module_type='short_reading'"
                    )
                )
                await session.execute(
                    text("UPDATE user_account SET status='SUSPENDED' WHERE public_id=:id"),
                    {"id": user},
                )
            with pytest.raises(AppError) as inactive:
                await authority.preview(user, readings[0])
            assert inactive.value.status_code == 403
            async with sessions() as session:
                internal = await session.scalar(
                    text("SELECT id FROM user_account WHERE public_id=:id"), {"id": user}
                )
                for table in (
                    "short_reading_progress",
                    "learning_completion_event",
                    "daily_checkin",
                    "user_contact",
                ):
                    assert (
                        await session.scalar(
                            text(f"SELECT COUNT(*) FROM {table} WHERE user_id=:user"),
                            {"user": internal},
                        )
                        == 0
                    )
    finally:
        async with sessions() as session, session.begin():
            if old_modules is not None:
                for module, enabled in old_modules:
                    await session.execute(
                        text(
                            "UPDATE learning_module_config SET enabled=:enabled "
                            "WHERE module_type=:module"
                        ),
                        {"module": module, "enabled": enabled},
                    )
                if not any(module == "short_reading" for module, _ in old_modules):
                    await session.execute(
                        text("DELETE FROM learning_module_config WHERE module_type='short_reading'")
                    )
            for identity in readings:
                await session.execute(
                    text("DELETE FROM short_reading_revision WHERE short_reading_id=:id"),
                    {"id": identity},
                )
                await session.execute(
                    text("DELETE FROM short_reading WHERE public_id=:id"), {"id": identity}
                )
                await session.execute(
                    text(
                        "DELETE FROM content_identity WHERE content_type='short_reading' "
                        "AND content_id=:id"
                    ),
                    {"id": identity},
                )
            await session.execute(
                text("DELETE FROM media_asset WHERE public_id=:id"), {"id": cover}
            )
            await session.execute(
                text("DELETE FROM content_series WHERE public_id=:id"), {"id": series}
            )
            await session.execute(
                text("DELETE FROM user_account WHERE public_id=:id"), {"id": user}
            )
        await redis.aclose()
        await engine.dispose()
