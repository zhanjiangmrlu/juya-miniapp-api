"""Actual admin routes/HMAC/Redis/SQL; the external OSS signer is a test port."""

import json
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
from juya_miniapp_api.modules.favorites.snapshots import SnapshotReference, SnapshotService
from juya_miniapp_api.modules.favorites.snapshots_sql import SQLAlchemySnapshotRepository
from juya_miniapp_api.shared.errors import AppError
from juya_miniapp_api.shared.ids import new_ulid

ADMIN_SRC = Path(__file__).resolve().parents[3] / "juya-admin-api" / "src"


@pytest.mark.asyncio
async def test_hmac_authority_snapshot_final_facts_source_gate_and_review_independence(
    monkeypatch,
) -> None:
    url = os.getenv("JUYA_TEST_DATABASE_URL")
    redis_url = os.getenv("JUYA_TEST_REDIS_URL")
    if not url or not redis_url:
        pytest.skip("isolated MySQL and Redis required")
    admin_src = ADMIN_SRC
    sys.path.insert(0, str(admin_src))
    admin_dependencies = (
        admin_src.parent
        / ".venv"
        / (
            "Lib/site-packages"
            if os.name == "nt"
            else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        )
    )
    sys.path.append(str(admin_dependencies))
    from juya_admin_api.infrastructure.security.service_hmac import (
        RedisNonceStore,
        verify_request_signature,
    )
    from juya_admin_api.modules.pronunciation.domain import cache_key
    from juya_admin_api.modules.v15_access.router import create_v15_access_router
    from juya_admin_api.shared.errors import AppError as AdminError

    engine = create_engine(url.replace("mysql+pymysql://", "mysql+asyncmy://", 1))
    sessions = create_session_factory(engine)
    redis = Redis.from_url(redis_url, decode_responses=True)
    now = datetime.now(UTC)
    secret = b"isolated-contract-key-never-production"
    user, scene, revision, series = [new_ulid(now) for _ in range(4)]
    contents = {
        "schema_version": 2,
        "template_type": "word_card",
        "title_en": "Coffee",
        "title_zh": "咖啡店",
        "vocabulary": [
            {
                "entry_id": "medium-cup",
                "sense_id": "cup",
                "source_id": "cup-source",
                "english": "medium",
                "chinese": "中杯",
                "source_sentence": "A medium coffee.",
                "confirmed": True,
                "pronunciation_target_id": "cup-audio",
                "audio_version_id": "cup-v1",
            },
            {
                "entry_id": "medium-size",
                "sense_id": "size",
                "source_id": "size-source",
                "english": "medium",
                "chinese": "中等的",
                "source_sentence": "A medium size.",
                "confirmed": True,
            },
        ],
    }
    try:
        async with sessions() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO user_account(public_id,juya_number,status) "
                    "VALUES(:id,:number,'ACTIVE')"
                ),
                {"id": user, "number": user[-12:]},
            )
            await session.execute(
                text(
                    "INSERT INTO "
                    "learning_module_config(id,module_type,display_name,sort_order,enabled) "
                    "VALUES(101,'scene_learning','场景',1,1) ON DUPLICATE KEY UPDATE enabled=1"
                )
            )
            await session.execute(
                text(
                    "INSERT INTO content_series(public_id,slug,title,status) "
                    "VALUES(:id,:id,'contract fixture','PUBLISHED')"
                ),
                {"id": series},
            )
            series_id = await session.scalar(text("SELECT LAST_INSERT_ID()"))
            await session.execute(
                text(
                    "INSERT INTO "
                    "content_template(template_type,version,required_modules,validation_rules) "
                    "VALUES('word_card',101,'[]','{}')"
                )
            )
            template_id = await session.scalar(text("SELECT LAST_INSERT_ID()"))
            await session.execute(
                text(
                    "INSERT INTO scene(public_id,series_id,template_id,title,status) "
                    "VALUES(:id,:series,:template,'contract fixture','PUBLISHED')"
                ),
                {"id": scene, "series": series_id, "template": template_id},
            )
            scene_id = await session.scalar(text("SELECT LAST_INSERT_ID()"))
            await session.execute(
                text(
                    "INSERT INTO "
                    "scene_revision(public_id,scene_id,version_no,status,content_snapshot,"
                    "created_by) VALUES(:id,:scene,1,'PUBLISHED',:content,:actor)"
                ),
                {"id": revision, "scene": scene_id, "content": json.dumps(contents), "actor": user},
            )
            revision_id = await session.scalar(text("SELECT LAST_INSERT_ID()"))
            await session.execute(
                text("UPDATE scene SET published_revision_id=:r WHERE id=:id"),
                {"r": revision_id, "id": scene_id},
            )
            await session.execute(
                text(
                    "INSERT INTO open_scene_config(version,activated_at,actor_public_id) "
                    "VALUES(101,UTC_TIMESTAMP(6),:actor)"
                ),
                {"actor": user},
            )
            config_id = await session.scalar(text("SELECT LAST_INSERT_ID()"))
            await session.execute(
                text(
                    "INSERT INTO open_scene_item(config_id,scene_id,position) "
                    "VALUES(:config,:scene,1)"
                ),
                {"config": config_id, "scene": scene_id},
            )

        async def signer(session, request, expiry):
            # The authorization and fixed resource role are real. No external OSS call.
            return {
                "target_id": request.target_id,
                "resource_version": request.resource_version,
                "url": "https://media.invalid/test-only",
                "expires_at": expiry.isoformat(),
            }

        from types import SimpleNamespace

        from juya_admin_api.infrastructure import v15_runtime
        from juya_admin_api.infrastructure.config import Settings as AdminSettings
        from juya_admin_api.modules.pronunciation.router import create_router as pron_router

        from juya_miniapp_api.modules.pronunciation.adapters import (
            CurrentContentAdapter,
            SnapshotReviewAdapter,
        )
        from juya_miniapp_api.modules.pronunciation.domain import PronunciationRequest
        from juya_miniapp_api.modules.pronunciation.service import PronunciationService

        # Replace only the external OSS signing port; use actual runtime authorization callbacks.
        monkeypatch.setattr(v15_runtime, "SQLResourceSigner", lambda *args, **kwargs: signer)
        runtime = v15_runtime.build_v15_services(
            AdminSettings(
                _env_file=None,
                environment="local",
                internal_hmac_secret=SecretStr(secret.decode()),
                content_security_enabled=False,
                content_ai_enabled=False,
                asr_enabled=False,
                import_storage_path="isolated-unused-imports",
            ),
            sessions,
            object(),
            SimpleNamespace(get_asset=None),
            object(),
        )
        service = runtime.access
        admin = FastAPI()
        nonce_store = RedisNonceStore(redis, key_prefix="v15-test:" + user)

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
        admin.include_router(pron_router(runtime.pronunciation, principal))
        async with httpx.AsyncClient(
            base_url="http://admin", transport=httpx.ASGITransport(app=admin)
        ) as http:
            client = AdminV15Client(http, secret=secret)
            guard = FinalFactsGuard(secret)
            repo = SQLAlchemySnapshotRepository(sessions, guard.snapshot)
            snapshots = SnapshotService(repo, client)
            ref = SnapshotReference(
                content_type="word_card",
                content_id=scene,
                content_version=1,
                entry_id="medium-cup",
                sense_id="cup",
                source_id="cup-source",
            )
            first = await snapshots.create(user, ref, "first", datetime.now(UTC))
            second = await snapshots.create(
                user,
                ref.model_copy(
                    update={
                        "entry_id": "medium-size",
                        "sense_id": "size",
                        "source_id": "size-source",
                    }
                ),
                "second",
                datetime.now(UTC),
            )
            assert first.favorite_id != second.favorite_id
            assert first.data.meaning_zh == "中杯" and second.data.meaning_zh == "中等的"
            assert (await client.source_resolution(user, first.favorite_id)).status == "EXACT"
            from juya_miniapp_api.modules.favorites.compatibility import LegacyFavoriteAdapter
            from juya_miniapp_api.modules.favorites.repository import SQLAlchemyFavoriteRepository
            from juya_miniapp_api.modules.learning.v15_authority import V15SceneAuthority

            compat = LegacyFavoriteAdapter(
                snapshots,
                repo,
                SQLAlchemyFavoriteRepository(sessions),
                V15SceneAuthority(sessions, client),
            )
            old_request = {
                "scene_id": scene,
                "revision_id": revision,
                "entry_type": "VOCABULARY",
                "entry_stable_id": "medium-cup",
                "entry_version": 1,
                "source_locator": "cup-source",
                "text": "forged spelling",
                "sentence_snapshot": "forged sentence",
            }
            saved = await compat.create(user, old_request, datetime.now(UTC), "legacy-create")
            assert saved["id"] == first.favorite_id and saved["normalized_key"] == "medium"
            assert saved["sources"][0]["sentence_snapshot"] == "A medium coffee."
            with pytest.raises(AppError) as wrong_locator:
                await compat.create(
                    user,
                    old_request | {"source_locator": "wrong"},
                    datetime.now(UTC),
                    "legacy-wrong",
                )
            assert wrong_locator.value.status_code == 409
            async with sessions() as session, session.begin():
                await session.execute(
                    text(
                        "INSERT INTO pronunciation_target(public_id,confirmed_text,"
                        "reading_variant,language,"
                        "target_type,allow_synthesis,uploaded_target_id,uploaded_resource_version,status,"
                        "config_version,confirmed_by,confirmed_at,created_at) "
                        "VALUES('cup-audio','medium',"
                        "'','en','WORD',0,'cup-audio','cup-v1','CONFIRMED','1',:actor,1,1)"
                    ),
                    {"actor": user},
                )
            pronunciation = PronunciationService(
                SnapshotReviewAdapter(snapshots),
                CurrentContentAdapter(V15SceneAuthority(sessions, client)),
                client,
            )
            current_ref = PronunciationRequest(
                content_type="word_card", content_id=scene, content_version=1, entry_id="medium-cup"
            )
            assert (await pronunciation.resolve(user, "cup-audio", current_ref)).status == "READY"
            # Arrange private task rows only; no worker/cloud/budget invocation.
            target = await runtime.pronunciation.repository.target("cup-audio")
            assert target is not None
            task = await runtime.pronunciation.repository.enqueue(
                target,
                cache_key(target, runtime.pronunciation.config),
                runtime.pronunciation.config,
            )
            for internal_state in ("RUNNING", "RECOVER"):
                async with sessions() as session, session.begin():
                    await session.execute(
                        text("UPDATE pronunciation_task SET status=:status WHERE public_id=:id"),
                        {"status": internal_state, "id": task["public_id"]},
                    )
                preparing = await pronunciation.poll(user, task["public_id"], current_ref)
                assert preparing.status == "PREPARING" and preparing.url is None
                assert not preparing.can_retry
            signed = await V15SceneAuthority(sessions, client).signed_resource(
                user, scene, "cup-audio", revision, datetime.now(UTC)
            )
            assert signed.resource_id == "cup-audio"
            async with sessions() as session:
                event_rows = (
                    (
                        await session.execute(
                            text(
                                "SELECT payload FROM miniapp_outbox WHERE aggregate_id=:id "
                                "ORDER BY created_at,id"
                            ),
                            {"id": first.favorite_id},
                        )
                    )
                    .scalars()
                    .all()
                )
            first_event = (
                json.loads(event_rows[0]) if isinstance(event_rows[0], str) else event_rows[0]
            )
            assert (await client.project_favorite_reference(first_event))["applied"]
            assert not (await client.project_favorite_reference(first_event))["applied"]
            confirmed = await client.confirm_snapshot(user, ref)
            async with sessions() as session, session.begin():
                await session.execute(
                    text(
                        "UPDATE learning_module_config SET enabled=0 "
                        "WHERE module_type='scene_learning'"
                    )
                )
            with pytest.raises(AppError):
                async with sessions() as session, session.begin():
                    await guard.snapshot(session, user, confirmed)
            assert (await client.source_resolution(user, first.favorite_id)).status == "NO_ACCESS"
            assert (await snapshots.detail(user, first.favorite_id)).data.meaning_zh == "中杯"
            assert (await snapshots.review_context(user, first.favorite_id, "cup-audio"))[
                "purpose"
            ] == "FAVORITE_REVIEW"
            assert (
                await pronunciation.resolve(
                    user, "cup-audio", PronunciationRequest(favorite_id=first.favorite_id)
                )
            ).status == "READY"
            with pytest.raises(AppError):
                await pronunciation.resolve(user, "cup-audio", current_ref)
            resource = await client._request_json(
                "POST",
                f"/internal/v1/favorite-snapshots/{first.favorite_id}/resources/cup-audio/signed-url",
                {"user_id": user, "resource_version": "cup-v1"},
            )
            assert resource["target_id"] == "cup-audio"
            await snapshots.cancel(user, first.favorite_id, "cancel", datetime.now(UTC))
            with pytest.raises(AppError):
                await client._request_json(
                    "POST",
                    f"/internal/v1/favorite-snapshots/{first.favorite_id}/resources/cup-audio/signed-url",
                    {"user_id": user, "resource_version": "cup-v1"},
                )
            with pytest.raises(AppError):
                await pronunciation.resolve(
                    user, "cup-audio", PronunciationRequest(favorite_id=first.favorite_id)
                )
            async with sessions() as session:
                release = await session.scalar(
                    text(
                        "SELECT payload FROM miniapp_outbox WHERE aggregate_id=:id "
                        "ORDER BY created_at DESC,id DESC LIMIT 1"
                    ),
                    {"id": first.favorite_id},
                )
            release = json.loads(release) if isinstance(release, str) else release
            from juya_miniapp_api.infrastructure.tasks.outbox import (
                OutboxDispatcher,
                SQLAlchemyOutboxStore,
            )
            from juya_miniapp_api.modules.accounts.tasks import create_account_cleanup_handler

            dispatcher = OutboxDispatcher(
                SQLAlchemyOutboxStore(sessions), create_account_cleanup_handler(client)
            )
            assert await dispatcher.dispatch_due(datetime.now(UTC)) >= 3
            async with sessions() as session:
                assert (
                    await session.scalar(
                        text("SELECT status FROM miniapp_outbox WHERE id=:id"),
                        {"id": release["event_id"]},
                    )
                    == "DELIVERED"
                )
            assert not (await client.project_favorite_reference(release))["applied"]
            assert (await client.project_favorite_reference(first_event))["reason"] == "STALE"
            async with sessions() as session:
                assert not await session.scalar(
                    text("SELECT active FROM favorite_resource_projection WHERE favorite_id=:id"),
                    {"id": first.favorite_id},
                )
            # Unsigned delegation is rejected by the actual admin HMAC dependency.
            assert (
                await http.post(
                    "/internal/v1/favorite-snapshots/confirmation",
                    json={"user_id": user, "reference": ref.model_dump(exclude={"entry_type"})},
                )
            ).status_code == 401
            from juya_admin_api.modules.content.v15_backfill import FavoriteBackfill

            from juya_miniapp_api.modules.accounts.repository import SQLAlchemyAccountRepository
            from juya_miniapp_api.modules.favorites.repository import SQLAlchemyFavoriteRepository
            from juya_miniapp_api.modules.favorites.service import FavoriteService

            old = await FavoriteService(SQLAlchemyFavoriteRepository(sessions)).favorite(
                user,
                "VOCABULARY",
                "old saved word",
                "old-entry",
                "missing-scene",
                "saved sentence",
                "old-locator",
                datetime.now(UTC),
                entry_snapshot={"english": "old saved word"},
            )
            await FavoriteBackfill(sessions).run(execute=True)
            rows = (await snapshots.page(user, None, None, 100)).items
            legacy = next(row for row in rows if row.data.reference.entry_id == "old-entry")
            assert legacy.data.legacy_incomplete
            assert legacy.saved_content_version is None and legacy.data.revision_id is None
            assert legacy.data.meaning_zh == "" and "meaning_zh" in legacy.data.missing_fields
            await compat.delete(user, old.public_id, datetime.now(UTC))
            await compat.delete(user, old.public_id, datetime.now(UTC))
            assert not (await repo.get(user, legacy.favorite_id)).active
            multi_service = FavoriteService(SQLAlchemyFavoriteRepository(sessions))
            multi = await multi_service.favorite(
                user,
                "VOCABULARY",
                "medium",
                "multi-entry",
                "missing-a",
                "one",
                "locator-one",
                datetime.now(UTC),
                entry_snapshot={"english": "medium"},
            )
            await multi_service.favorite(
                user,
                "VOCABULARY",
                "medium",
                "multi-entry",
                "missing-b",
                "two",
                "locator-two",
                datetime.now(UTC),
                entry_snapshot={"english": "medium"},
            )
            await FavoriteBackfill(sessions).run(execute=True)
            before = await snapshots.page(user, None, None, 100)
            with pytest.raises(AppError) as ambiguous:
                await compat.delete(user, multi.public_id, datetime.now(UTC))
            assert ambiguous.value.status_code == 409
            assert (await snapshots.page(user, None, None, 100)).items == before.items
            with pytest.raises(AppError) as detail_ambiguous:
                await compat.detail(user, multi.public_id)
            assert detail_ambiguous.value.status_code == 409
            with pytest.raises(AppError):
                await compat.review_ids(user, [multi.public_id])
            from juya_miniapp_api.modules.users.repository import SQLAlchemyUserRepository

            totals = await SQLAlchemyUserRepository(sessions).learning_achievements(user)
            assert totals["favorite_vocabulary"] == 3 and totals["favorite_phrases"] == 0
            projected = await compat.listing(user, None, 100)
            assert len(projected["items"]) == len(before.items)
            assert not any(item["id"] == multi.public_id for item in projected["items"])
            known = await multi_service.favorite(
                user,
                "VOCABULARY",
                "saved historical",
                "known-entry",
                scene,
                "saved historical sentence",
                "unresolved-old",
                datetime.now(UTC),
                revision_id=revision,
                entry_version=1,
                entry_snapshot={"english": "saved historical", "chinese": "旧资料"},
            )
            await FavoriteBackfill(sessions).run(execute=True)
            known_id = await compat.resolve(user, known.public_id)
            assert (await compat.source_resolution(user, known_id)).status == "NO_ACCESS"
            async with sessions() as session, session.begin():
                await session.execute(
                    text(
                        "UPDATE learning_module_config SET enabled=1 "
                        "WHERE module_type='scene_learning'"
                    )
                )
            current_source = await compat.source_resolution(user, known_id)
            assert current_source.status == "UPDATED_OR_UNRESOLVED"
            assert current_source.target_content_version == 1 and current_source.locator is None
            assert (await snapshots.detail(user, known_id)).data.english == "saved historical"
            await SQLAlchemyAccountRepository(sessions).clear_learning_data(user)
            assert (await snapshots.page(user, None, None, 100)).items == []
    finally:
        if "runtime" in locals():
            await runtime.client.aclose()
        await redis.aclose()
        await engine.dispose()
