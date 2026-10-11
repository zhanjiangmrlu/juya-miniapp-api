"""Existing legacy content remains recordable under the actual global HMAC switch."""

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory

_SPEC = importlib.util.spec_from_file_location(
    "v15_legacy_recording_testkit", Path(__file__).parents[2] / "scripts/v15_socket_testkit.py"
)
assert _SPEC and _SPEC.loader
_KIT = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _KIT
_SPEC.loader.exec_module(_KIT)
PrivateResources, SocketTestKit = _KIT.PrivateResources, _KIT.SocketTestKit


async def configuration(kit, value):
    kit.resources.verify_containers()
    engine = create_engine(
        kit.resources.database_url.get_secret_value().replace(
            "mysql+pymysql://", "mysql+asyncmy://", 1
        )
    )
    try:
        async with create_session_factory(engine)() as sql, sql.begin():
            if value is None:
                await sql.execute(
                    text("DELETE FROM system_config WHERE config_key='learning_features_v15'")
                )
            else:
                await sql.execute(
                    text(
                        "INSERT INTO system_config(config_key,value,version) "
                        "VALUES('learning_features_v15',:value,1) "
                        "ON DUPLICATE KEY UPDATE value=:value,version=version+1"
                    ),
                    {"value": json.dumps(value)},
                )
    finally:
        await engine.dispose()


async def legacy_content(kit):
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
                    "UPDATE scene_revision SET content_snapshot="
                    "JSON_SET(content_snapshot,'$.schema_version',1) WHERE public_id=:revision"
                ),
                {"revision": kit.revision_ids[0]},
            )
    finally:
        await engine.dispose()


def test_real_legacy_scene_recording_switch_toggle_missing_and_malformed():
    if not os.getenv("JUYA_TEST_DATABASE_URL") or not os.getenv("JUYA_TEST_REDIS_URL"):
        pytest.skip("central UUID-isolated runner required")
    with SocketTestKit(PrivateResources.from_environment()) as kit:
        asyncio.run(legacy_content(kit))
        identity = kit.issue_session()
        try:
            with kit.client(identity) as client:
                for value, expected in (
                    ({"recording_enabled": True, "profile_completion_enabled": True}, True),
                    ({"recording_enabled": False}, False),
                    (None, False),
                    ({"recording_enabled": "true"}, False),
                ):
                    asyncio.run(configuration(kit, value))
                    response = client.post(
                        f"/api/v1/scenes/{kit.scene_ids[0]}/open",
                        headers={"Idempotency-Key": "legacy-recording-same-key"},
                    )
                    assert response.status_code == 200
                    assert "no-store" in response.headers["Cache-Control"]
                    body = response.json()
                    assert body["access"] == "OPEN" and not body["authorization_pending"]
                    assert body["scene"]["content"]["schema_version"] == 1
                    assert body["scene"]["content"]["dialogue"]
                    assert body["scene"]["recording_enabled"] is expected
                    catalog = client.get("/api/v1/learning/catalog")
                    assert catalog.status_code == 200
                    assert "no-store" in catalog.headers["Cache-Control"]
                    assert catalog.json()["profile_completion_enabled"] is (
                        isinstance(value, dict) and value.get("profile_completion_enabled") is True
                    )
        finally:
            asyncio.run(configuration(kit, {"recording_enabled": False}))
