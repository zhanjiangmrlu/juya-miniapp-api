"""Coordinator's exact preparable-target fixture through production socket/JWT/HMAC/SQL."""

import asyncio
import importlib.util
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory
from juya_miniapp_api.integrations.admin_api.v15_client import AdminV15Client
from juya_miniapp_api.integrations.admin_api.v15_facts import SQLAuthorizer
from juya_miniapp_api.integrations.admin_api.v15_guard import confirmed_entry, scene_revision
from juya_miniapp_api.modules.favorites.snapshots import SnapshotReference, request_digest
from juya_miniapp_api.shared.ids import new_ulid

_SPEC = importlib.util.spec_from_file_location(
    "v15_confirmation_testkit", Path(__file__).parents[2] / "scripts/v15_socket_testkit.py"
)
assert _SPEC and _SPEC.loader
_KIT = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _KIT
_SPEC.loader.exec_module(_KIT)


async def fixture_and_confirmation(kit, user):
    kit.resources.verify_containers()
    database = kit.resources.database_url.get_secret_value()
    engine = create_engine(database.replace("mysql+pymysql://", "mysql+asyncmy://", 1))
    sessions = create_session_factory(engine)
    now = datetime.now(UTC)
    scene, revision, series, target = [new_ulid(now) for _ in range(4)]
    content = {
        "schema_version": 2,
        "template_type": "dialogue",
        "title_en": "Coffee Dialogue",
        "title_zh": "咖啡对话",
        "dialogue": [
            {
                "id": "cup-sentence",
                "speaker": "A",
                "english": "A medium coffee to take away.",
                "chinese": "一杯中杯咖啡，打包。",  # noqa: RUF001 - exact coordinator fixture
            }
        ],
        "vocabulary": [
            {
                "entry_id": "medium-cup",
                "sense_id": "cup",
                "source_id": "cup-source",
                "english": "medium",
                "chinese": "中杯",
                "source_sentence": "A medium coffee to take away.",
                "source_locator": {"sentence_id": "cup-sentence"},
                "pronunciation_target_id": target,
                "allow_synthesis": True,
                "confirmed": True,
            }
        ],
        "chunks": [],
    }
    try:
        async with sessions() as sql, sql.begin():
            await sql.execute(
                text(
                    "INSERT INTO content_series(public_id,slug,title,status) "
                    "VALUES(:id,:id,'confirmation fixture','PUBLISHED')"
                ),
                {"id": series},
            )
            series_pk = await sql.scalar(text("SELECT LAST_INSERT_ID()"))
            await sql.execute(
                text(
                    "INSERT IGNORE INTO content_template(template_type,version,"
                    "required_modules,validation_rules) "
                    "VALUES('socket-confirmation',1,JSON_ARRAY(),JSON_OBJECT())"
                )
            )
            template = await sql.scalar(
                text(
                    "SELECT id FROM content_template "
                    "WHERE template_type='socket-confirmation' AND version=1"
                )
            )
            await sql.execute(
                text(
                    "INSERT INTO scene(public_id,series_id,template_id,title,status) "
                    "VALUES(:id,:series,:template,'咖啡对话','PUBLISHED')"
                ),
                {"id": scene, "series": series_pk, "template": template},
            )
            scene_pk = await sql.scalar(text("SELECT LAST_INSERT_ID()"))
            await sql.execute(
                text(
                    "INSERT INTO scene_revision(public_id,scene_id,version_no,status,"
                    "content_snapshot,created_by) "
                    "VALUES(:id,:scene,1,'PUBLISHED',:content,:actor)"
                ),
                {"id": revision, "scene": scene_pk, "content": json.dumps(content), "actor": user},
            )
            revision_pk = await sql.scalar(text("SELECT LAST_INSERT_ID()"))
            await sql.execute(
                text("UPDATE scene SET published_revision_id=:r WHERE id=:id"),
                {"r": revision_pk, "id": scene_pk},
            )
            version = await sql.scalar(
                text("SELECT COALESCE(MAX(version),0)+1 FROM open_scene_config")
            )
            await sql.execute(
                text(
                    "INSERT INTO open_scene_config(version,activated_at,actor_public_id) "
                    "VALUES(:version,UTC_TIMESTAMP(6),:actor)"
                ),
                {"version": version, "actor": user},
            )
            config = await sql.scalar(text("SELECT LAST_INSERT_ID()"))
            await sql.execute(
                text(
                    "INSERT INTO open_scene_item(config_id,scene_id,position) "
                    "VALUES(:config,:scene,1)"
                ),
                {"config": config, "scene": scene_pk},
            )
            await sql.execute(
                text(
                    "INSERT INTO pronunciation_target(public_id,confirmed_text,"
                    "reading_variant,language,target_type,allow_synthesis,status,"
                    "config_version,confirmed_by,confirmed_at,created_at) "
                    "VALUES(:id,'medium','','en','WORD',1,'CONFIRMED','1',:actor,1,1)"
                ),
                {"id": target, "actor": user},
            )
        ref = SnapshotReference(
            content_type="dialogue",
            content_id=scene,
            content_version=1,
            entry_id="medium-cup",
            sense_id="cup",
            source_id="cup-source",
        )
        async with httpx.AsyncClient(base_url=kit.admin_base_url, trust_env=False) as http:
            authority = AdminV15Client(http, secret=kit._hmac_secret.encode())
            confirmed = await authority.confirm_snapshot(user, ref)
        claims = confirmed._confirmation_credential
        assert claims is not None
        async with sessions() as sql:
            fixed_revision, fixed_version, fixed_content = await scene_revision(sql, scene)
            entry = confirmed_entry(fixed_content, confirmed)
            facts = await SQLAuthorizer()(
                sql, user, "dialogue", scene, datetime.now(UTC), lock=False
            )
        matches = {
            "revision": claims["revision_id"] == fixed_revision,
            "version": ref.content_version == fixed_version,
            "entry_hash": claims["details_hash"]
            == request_digest(
                entry.model_dump(mode="json", exclude={"is_favorited", "favorite_id"})
            ),
            "module_version": claims["module_version"] == facts.module_version,
            "authorization_version": claims["authorization_version"] == facts.authorization_version,
        }
        assert all(matches.values()), matches
        return ref, confirmed, config
    finally:
        await engine.dispose()


async def remove_owned_open_config(kit, user):
    kit.resources.verify_containers()
    engine = create_engine(
        kit.resources.database_url.get_secret_value().replace(
            "mysql+pymysql://", "mysql+asyncmy://", 1
        )
    )
    try:
        async with create_session_factory(engine)() as sql, sql.begin():
            await sql.execute(
                text(
                    "DELETE i FROM open_scene_item i JOIN open_scene_config c "
                    "ON c.id=i.config_id WHERE c.actor_public_id=:actor"
                ),
                {"actor": user},
            )
            await sql.execute(
                text("DELETE FROM open_scene_config WHERE actor_public_id=:actor"), {"actor": user}
            )
    finally:
        await engine.dispose()


def test_socket_preparable_target_locator_confirmation_matches_final_facts_without_cloud():
    if not os.getenv("JUYA_TEST_DATABASE_URL"):
        pytest.skip("central UUID-isolated runner required")
    resources = _KIT.PrivateResources.from_environment()
    with _KIT.SocketTestKit(resources) as kit:
        identity = kit.issue_session()
        try:
            ref, confirmed, _ = asyncio.run(fixture_and_confirmation(kit, identity.user_id))
            assert confirmed.pronunciation.can_prepare
            assert confirmed.pronunciation.resource_version is None
            with kit.client(identity) as client:
                opened = client.post(
                    f"/api/v1/scenes/{ref.content_id}/open",
                    headers={"Idempotency-Key": "preparable-open"},
                )
                assert opened.status_code == 200, opened.text
                body = ref.model_dump(exclude={"entry_type"})
                saved = client.post(
                    "/api/v1/favorite-snapshots",
                    json=body,
                    headers={"Idempotency-Key": "preparable-save"},
                )
                assert saved.status_code == 201, saved.text
                assert saved.json()["meaning_zh"] == "中杯"
                assert saved.json()["source_locator"] == {"sentence_id": "cup-sentence"}
                assert saved.json()["pronunciation"]["can_prepare"]
                replay = client.post(
                    "/api/v1/favorite-snapshots",
                    json=body,
                    headers={"Idempotency-Key": "preparable-save"},
                )
                assert replay.json()["favorite_id"] == saved.json()["favorite_id"]
        finally:
            asyncio.run(remove_owned_open_config(kit, identity.user_id))
