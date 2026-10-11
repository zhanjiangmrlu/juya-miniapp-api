"""Private SQL + actual admin HMAC/Redis routes; no publication or OSS acceptance."""

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import text

from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory
from juya_miniapp_api.integrations.admin_api.v15_client import AdminV15Client
from juya_miniapp_api.integrations.admin_api.v15_guard import FinalFactsGuard
from juya_miniapp_api.modules.short_reading.authority import V15ReadingAuthority
from juya_miniapp_api.modules.short_reading.domain import ReadingPosition
from juya_miniapp_api.modules.short_reading.history import SQLReadingHistory
from juya_miniapp_api.modules.short_reading.router import create_short_reading_router
from juya_miniapp_api.modules.short_reading.service import ShortReadingService
from juya_miniapp_api.modules.short_reading.sql_repository import SQLAlchemyReadingRepository
from juya_miniapp_api.shared.errors import AppError
from juya_miniapp_api.shared.ids import new_ulid

ADMIN_SRC = Path(__file__).resolve().parents[3] / "juya-admin-api" / "src"


@pytest.mark.asyncio
async def test_reading_actual_hmac_preview_grants_progress_completion_and_module_gate() -> None:
    url = os.getenv("JUYA_TEST_DATABASE_URL")
    redis_url = os.getenv("JUYA_TEST_REDIS_URL")
    if not url or not redis_url:
        pytest.skip("isolated MySQL and Redis required")
    admin_src = ADMIN_SRC
    sys.path.insert(0, str(admin_src))
    sys.path.append(str(admin_src.parent / ".venv" / "Lib" / "site-packages"))
    from juya_admin_api.infrastructure.security.service_hmac import (
        RedisNonceStore,
        verify_request_signature,
    )
    from juya_admin_api.modules.short_reading.store import ShortReadingStore
    from juya_admin_api.modules.v15_access.router import create_v15_access_router
    from juya_admin_api.modules.v15_access.service import V15AccessService
    from juya_admin_api.modules.v15_access.sql_adapters import (
        SQLAuthorizer,
        SQLContentLoader,
        SQLFavoriteAuthority,
    )
    from juya_admin_api.shared.errors import AppError as AdminError

    engine = create_engine(url.replace("mysql+pymysql://", "mysql+asyncmy://", 1))
    sessions = create_session_factory(engine)
    redis = Redis.from_url(redis_url, decode_responses=True)
    now = datetime.now(UTC)
    user, stranger, series, package, entitlement = [new_ulid(now) for _ in range(5)]
    secret = b"private-reading-contract-key-not-production"
    store = ShortReadingStore(sessions)
    fixture = {
        "title_en": "A Private Fixture",
        "title_zh": "隔离短文",
        "category": "daily",
        "topic": "coffee",
        "level": "A1",
        "safe_intro": "Safe introduction only.",
        "paragraphs_en": ["The hidden first paragraph.", "The hidden second paragraph."],
        "paragraph_ids": ["first", "second"],
        "translation_zh": "仅供隔离测试的正文译文。",
        "audio": {
            "asset_id": "private-asset",
            "version_id": "private-version",
            "target_id": "private-target",
            "duration_ms": 1000,
        },
    }
    try:
        async with sessions() as session, session.begin():
            for owner in (user, stranger):
                await session.execute(
                    text(
                        "INSERT INTO user_account(public_id,juya_number,status) "
                        "VALUES(:id,:number,'ACTIVE')"
                    ),
                    {"id": owner, "number": "JY" + owner[-12:]},
                )
            internal_user = await session.scalar(
                text("SELECT id FROM user_account WHERE public_id=:id"), {"id": user}
            )
            await session.execute(
                text(
                    "INSERT INTO learning_module_config "
                    "(id,module_type,display_name,sort_order,enabled) "
                    "VALUES(102,'short_reading','短文',2,1) "
                    "ON DUPLICATE KEY UPDATE enabled=1"
                )
            )
            await session.execute(
                text(
                    "INSERT INTO content_series(public_id,slug,title,status) "
                    "VALUES(:id,:id,'reading integration fixture','PUBLISHED')"
                ),
                {"id": series},
            )
            created = await store.create_within(session, series, fixture, user)
            reading, revision = created["content_id"], created["revision_id"]
            # These are private DB published-state fixtures, not a manual publish assertion.
            await session.execute(
                text("UPDATE short_reading_revision SET status='PUBLISHED' WHERE public_id=:id"),
                {"id": revision},
            )
            await session.execute(
                text(
                    "UPDATE short_reading SET status='PUBLISHED',published_revision_id=:r "
                    "WHERE public_id=:id"
                ),
                {"r": revision, "id": reading},
            )

        signed_requests = []

        async def signer(session, request, expiry):
            signed_requests.append(request)
            return {
                "target_id": request.target_id,
                "resource_version": request.resource_version,
                "url": "https://media.invalid/private-test-only",
                "expires_at": expiry.isoformat(),
            }

        service = V15AccessService(
            sessions,
            content_loader=SQLContentLoader(store),
            authorizer=SQLAuthorizer(),
            resource_signer=signer,
            credential_secret=secret,
            favorite_authority=SQLFavoriteAuthority(),
        )
        admin = FastAPI()
        nonce_store = RedisNonceStore(redis, key_prefix="v15-reading-test:" + user)

        async def principal(request: Request):
            return await verify_request_signature(
                request, SecretStr(secret.decode()), nonce_store, datetime.now(UTC)
            )

        @admin.exception_handler(AdminError)
        async def admin_error(request, error):
            return JSONResponse(
                {"code": error.code, "message": error.message}, status_code=error.status_code
            )

        admin.include_router(create_v15_access_router(service, current_service=principal))
        async with httpx.AsyncClient(
            base_url="http://admin", transport=httpx.ASGITransport(app=admin)
        ) as delegated:
            client = AdminV15Client(delegated, secret=secret)
            authority = V15ReadingAuthority(sessions, client)
            repository = SQLAlchemyReadingRepository(sessions, FinalFactsGuard(secret).reading)
            reading_service = ShortReadingService(authority, repository)
            bff = FastAPI()

            async def owner():
                return user

            @bff.exception_handler(AppError)
            async def bff_error(request, error):
                return JSONResponse(
                    {"code": error.code, "message": error.message}, status_code=error.status_code
                )

            bff.include_router(
                create_short_reading_router(
                    reading_service,
                    authority,
                    user_dependency=owner,
                    history=SQLReadingHistory(sessions),
                )
            )
            async with httpx.AsyncClient(
                base_url="http://bff", transport=httpx.ASGITransport(app=bff)
            ) as public:
                prefix = f"/api/v1/short-readings/{reading}"
                preview = await public.get(prefix + "/preview")
                assert preview.status_code == 200 and preview.json()["access"] == "PREVIEW"
                assert preview.headers["cache-control"] == "no-store"
                assert not {
                    "paragraphs_en",
                    "paragraph_ids",
                    "translation_zh",
                    "whole_audio",
                    "audio",
                }.intersection(preview.json())
                assert "hidden" not in preview.text and "private-target" not in preview.text
                listing = await public.get("/api/v1/short-readings", params={"limit": 100})
                assert listing.status_code == 200 and listing.headers["cache-control"] == "no-store"
                listed = next(
                    row for row in listing.json()["items"] if row["content_id"] == reading
                )
                assert listed == preview.json()
                # Operational ordering comes from the actual admin-owned integer,
                # with public_id tie-breaking and a live order snapshot in the cursor.
                async with sessions() as session, session.begin():
                    extra = await store.create_within(session, series, fixture, user)
                    await session.execute(
                        text(
                            "UPDATE short_reading_revision SET status='PUBLISHED' "
                            "WHERE public_id=:r"
                        ),
                        {"r": extra["revision_id"]},
                    )
                    await session.execute(
                        text(
                            "UPDATE short_reading SET status='PUBLISHED',published_revision_id=:r,"
                            "sort_order=-2147483599 WHERE public_id=:id"
                        ),
                        {"id": extra["content_id"], "r": extra["revision_id"]},
                    )
                    await session.execute(
                        text("UPDATE short_reading SET sort_order=-2147483600 WHERE public_id=:id"),
                        {"id": reading},
                    )
                first_page = await public.get("/api/v1/short-readings", params={"limit": 1})
                assert first_page.json()["items"][0]["content_id"] == reading
                order_cursor = first_page.json()["next_cursor"]
                second_page = await public.get(
                    "/api/v1/short-readings", params={"limit": 1, "cursor": order_cursor}
                )
                assert second_page.json()["items"][0]["content_id"] == extra["content_id"]
                with pytest.raises(AppError) as other_cursor:
                    await authority.listing(stranger, order_cursor, 1)
                assert other_cursor.value.code == "CURSOR_INVALID"
                async with sessions() as session, session.begin():
                    await session.execute(
                        text("UPDATE short_reading SET sort_order=2 WHERE public_id=:id"),
                        {"id": reading},
                    )
                outdated = await public.get(
                    "/api/v1/short-readings", params={"limit": 1, "cursor": order_cursor}
                )
                assert outdated.status_code == 422 and outdated.json()["code"] == "CURSOR_INVALID"
                assert (await public.post(prefix + "/open")).status_code == 403
                assert (
                    await public.get(
                        prefix + "/resources/private-target/signed-url",
                        params={"revision_id": revision},
                    )
                ).status_code == 403
                assert not signed_requests
                assert (
                    await delegated.post(
                        f"/internal/v1/content/short_reading/{reading}/open", json={"user_id": user}
                    )
                ).status_code == 401

                async with sessions() as session, session.begin():
                    await session.execute(
                        text(
                            "INSERT INTO short_reading_trial "
                            "(short_reading_id,enabled,version,updated_by,updated_at) "
                            "VALUES(:id,1,1,:actor,UTC_TIMESTAMP(6))"
                        ),
                        {"id": reading, "actor": user},
                    )
                opened = await public.post(prefix + "/open")
                assert opened.status_code == 200, opened.text
                assert opened.json()["access"] == "TRIAL"
                assert opened.json()["paragraphs_en"][0]["paragraph_id"] == "first"
                audio = await public.get(
                    prefix + "/resources/private-target/signed-url",
                    params={"revision_id": revision},
                )
                assert audio.status_code == 200 and audio.headers["cache-control"] == "no-store"
                assert signed_requests[-1].role == "WHOLE"
                assert signed_requests[-1].resource_version == "private-version"
                assert (await authority.open_reading(stranger, reading)).access == "TRIAL"
                saved = await public.put(
                    prefix + "/progress",
                    json={
                        "content_version": 1,
                        "paragraph_id": "second",
                        "offset": 5,
                        "client_sequence": 3,
                    },
                )
                assert saved.status_code == 200 and saved.json()["offset"] == 5
                stale = await public.put(
                    prefix + "/progress",
                    json={
                        "content_version": 1,
                        "paragraph_id": "first",
                        "offset": 1,
                        "client_sequence": 2,
                    },
                )
                assert stale.json()["paragraph_id"] == "second" and stale.json()["offset"] == 5
                completed = await public.post(
                    prefix + "/complete",
                    json={"content_version": 1},
                    headers={"Idempotency-Key": "done"},
                )
                assert completed.status_code == 200 and completed.json()["created"]
                assert "paragraphs_en" not in completed.json()["progress"]
                completed_at = completed.json()["progress"]["completed_at"]

                async with sessions() as session, session.begin():
                    await session.execute(
                        text(
                            "UPDATE short_reading_trial SET enabled=0,version=version+1 "
                            "WHERE short_reading_id=:id"
                        ),
                        {"id": reading},
                    )
                    await session.execute(
                        text(
                            "INSERT INTO content_package(public_id,name,status) "
                            "VALUES(:id,'reading test package','ACTIVE')"
                        ),
                        {"id": package},
                    )
                    package_id = await session.scalar(text("SELECT LAST_INSERT_ID()"))
                    await session.execute(
                        text(
                            "INSERT INTO content_package_item(package_id,content_type,content_id,"
                            "position) VALUES(:package,'short_reading',:id,1)"
                        ),
                        {"package": package_id, "id": reading},
                    )
                    await session.execute(
                        text(
                            "INSERT INTO formal_entitlement(public_id,user_id,package_id,term,"
                            "status,granted_at,expires_at,updated_at) "
                            "VALUES(:id,:user,:package,'month_1','ACTIVE',UTC_TIMESTAMP(6),"
                            "DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 1 DAY),UTC_TIMESTAMP(6))"
                        ),
                        {"id": entitlement, "user": internal_user, "package": package_id},
                    )
                assert (await authority.open_reading(user, reading)).access == "FORMAL"
                with pytest.raises(AppError) as denied:
                    await authority.open_reading(stranger, reading)
                assert denied.value.status_code == 403
                assert await repository.get(stranger, reading) is None
                async with sessions() as session, session.begin():
                    await session.execute(
                        text(
                            "UPDATE formal_entitlement SET expires_at="
                            "DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 1 SECOND),version=version+1 "
                            "WHERE public_id=:id"
                        ),
                        {"id": entitlement},
                    )
                assert (await public.post(prefix + "/open")).status_code == 403
                assert (await public.get(prefix + "/preview")).json()["access"] == "PREVIEW"
                expired_replay = await public.post(
                    prefix + "/complete",
                    json={"content_version": 1},
                    headers={"Idempotency-Key": "done"},
                )
                assert expired_replay.status_code == 403
                summary = await public.get("/api/v1/short-readings/history")
                assert summary.status_code == 200, summary.text
                assert summary.json()["started_count"] == summary.json()["completed_count"] == 1
                assert summary.json()["items"][0]["title_zh"] == fixture["title_zh"]
                assert not summary.json()["items"][0]["can_open"]
                assert not {"paragraph_id", "offset", "whole_audio", "translation_zh"}.intersection(
                    summary.json()["items"][0]
                )
                assert (
                    await SQLReadingHistory(sessions).page(stranger, None, 20, datetime.now(UTC))
                ).started_count == 0
                async with sessions() as session, session.begin():
                    await session.execute(
                        text(
                            "UPDATE formal_entitlement SET expires_at="
                            "DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 1 DAY),version=version+1 "
                            "WHERE public_id=:id"
                        ),
                        {"id": entitlement},
                    )

                revised = new_ulid(datetime.now(UTC))
                async with sessions() as session, session.begin():
                    next_fixture = {**fixture, "paragraph_ids": ["third", "fourth"]}
                    await store.insert_revision(
                        session, reading, revised, next_fixture, user, 2, revision
                    )
                    await session.execute(
                        text(
                            "UPDATE short_reading_revision SET status='PUBLISHED' "
                            "WHERE public_id=:id"
                        ),
                        {"id": revised},
                    )
                    await session.execute(
                        text(
                            "UPDATE short_reading SET published_revision_id=:r,version=version+1 "
                            "WHERE public_id=:id"
                        ),
                        {"r": revised, "id": reading},
                    )
                reopened = await public.post(prefix + "/open")
                progress = reopened.json()["progress"]
                assert progress["saved_content_version"] == 2 and progress["paragraph_id"] is None
                assert progress["offset"] == 0 and progress["client_sequence"] == 0
                assert progress["completed_at"] == completed_at
                # Re-authorize the current publication but replay the original command metadata.
                versioned_replay = await public.post(
                    prefix + "/complete",
                    json={"content_version": 1},
                    headers={"Idempotency-Key": "done"},
                )
                assert versioned_replay.json() == completed.json()
                with pytest.raises(AppError) as old_guard:
                    await repository.open(user, reading, 1, {"first": 30}, datetime.now(UTC))
                assert old_guard.value.status_code == 409
                assert (
                    await public.get(
                        prefix + "/resources/private-target/signed-url",
                        params={"revision_id": revision},
                    )
                ).status_code == 409
                assert (
                    await public.put(
                        prefix + "/progress",
                        json={
                            "content_version": 1,
                            "paragraph_id": "first",
                            "offset": 1,
                            "client_sequence": 4,
                        },
                    )
                ).status_code == 409

                async with sessions() as session, session.begin():
                    await session.execute(
                        text(
                            "UPDATE learning_module_config SET enabled=0 "
                            "WHERE module_type='short_reading'"
                        )
                    )
                # Repository guard rejects after a previously successful authority read too.
                with pytest.raises(AppError) as guard_denied:
                    await repository.save(
                        user,
                        reading,
                        ReadingPosition(
                            content_version=2, paragraph_id="third", offset=2, client_sequence=1
                        ),
                        datetime.now(UTC),
                    )
                assert guard_denied.value.status_code == 403
                with pytest.raises(AppError) as open_guard:
                    await repository.open(user, reading, 2, {"third": 30}, datetime.now(UTC))
                assert open_guard.value.status_code == 403
                with pytest.raises(AppError) as complete_guard:
                    await repository.complete(
                        user, reading, "guard-denied", 2, {"third": 30}, datetime.now(UTC)
                    )
                assert complete_guard.value.status_code == 403
                for method, path, kwargs in [
                    ("GET", "/preview", {}),
                    ("POST", "/open", {}),
                    (
                        "PUT",
                        "/progress",
                        {
                            "json": {
                                "content_version": 2,
                                "paragraph_id": "third",
                                "offset": 1,
                                "client_sequence": 1,
                            }
                        },
                    ),
                    (
                        "GET",
                        "/resources/private-target/signed-url",
                        {"params": {"revision_id": revised}},
                    ),
                    (
                        "POST",
                        "/complete",
                        {
                            "json": {"content_version": 2},
                            "headers": {"Idempotency-Key": "new-denied"},
                        },
                    ),
                ]:
                    assert (
                        await public.request(method, prefix + path, **kwargs)
                    ).status_code == 403
                replay = await public.post(
                    prefix + "/complete",
                    json={"content_version": 1},
                    headers={"Idempotency-Key": "done"},
                )
                assert replay.status_code == 403
                assert (await public.get("/api/v1/short-readings")).status_code == 403
                history_closed = await public.get("/api/v1/short-readings/history")
                assert (
                    history_closed.status_code == 200
                    and history_closed.json()["completed_count"] == 1
                )
                assert not history_closed.json()["items"][0]["can_open"]
                async with sessions() as session, session.begin():
                    await session.execute(
                        text("UPDATE short_reading SET status='OFFLINE' WHERE public_id=:id"),
                        {"id": reading},
                    )
                history_offline = await public.get("/api/v1/short-readings/history")
                assert history_offline.json()["items"][0]["title_zh"] == fixture["title_zh"]
                assert history_offline.json()["completed_count"] == 1
                conflict = await public.post(
                    prefix + "/complete",
                    json={"content_version": 2},
                    headers={"Idempotency-Key": "done"},
                )
                assert conflict.status_code == 403
                for action in (
                    repository.replay_completion(user, reading, "done", 1),
                    repository.complete(user, reading, "done", 1, {"third": 30}, datetime.now(UTC)),
                ):
                    with pytest.raises(AppError) as blocked_replay:
                        await action
                    assert blocked_replay.value.status_code == 403
                async with sessions() as session:
                    events = (
                        (
                            await session.execute(
                                text(
                                    "SELECT event_type,COUNT(*) AS total FROM analytics_event "
                                    "WHERE user_id=:user AND event_type IN "
                                    "('SHORT_READING_STARTED','SHORT_READING_COMPLETED') "
                                    "GROUP BY event_type"
                                ),
                                {"user": internal_user},
                            )
                        )
                        .mappings()
                        .all()
                    )
                    assert {row["event_type"]: row["total"] for row in events} == {
                        "SHORT_READING_STARTED": 1,
                        "SHORT_READING_COMPLETED": 1,
                    }
                    for table in ("learning_completion_event", "daily_checkin", "user_contact"):
                        assert (
                            await session.scalar(
                                text(f"SELECT COUNT(*) FROM {table} WHERE user_id=:user"),
                                {"user": internal_user},
                            )
                            == 0
                        )
    finally:
        await redis.aclose()
        await engine.dispose()
