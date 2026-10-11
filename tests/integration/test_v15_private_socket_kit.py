"""Real socket runtime, JWT + owned SQL session and BFF -> admin HMAC delegation."""

import asyncio
import importlib.util
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory
from juya_miniapp_api.infrastructure.security.jwt_service import JwtService
from juya_miniapp_api.shared.ids import new_ulid

_SPEC = importlib.util.spec_from_file_location(
    "v15_socket_testkit", Path(__file__).resolve().parents[2] / "scripts/v15_socket_testkit.py"
)
assert _SPEC is not None and _SPEC.loader is not None
_KIT = sys.modules.get(_SPEC.name)
if _KIT is None:
    _KIT = importlib.util.module_from_spec(_SPEC)
    sys.modules[_SPEC.name] = _KIT
    _SPEC.loader.exec_module(_KIT)
FixtureSession, PrivateResources, SocketTestKit = (
    _KIT.FixtureSession,
    _KIT.PrivateResources,
    _KIT.SocketTestKit,
)


async def revoke_owned_fixture(resources: PrivateResources, identity: FixtureSession) -> None:
    resources.verify_containers()
    engine = create_engine(
        resources.database_url.get_secret_value().replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    )
    try:
        async with create_session_factory(engine)() as sql, sql.begin():
            row = (
                (
                    await sql.execute(
                        text(
                            "SELECT u.public_id,u.status,s.id FROM user_session s "
                            "JOIN user_account u ON u.id=s.user_id "
                            "WHERE s.id=:id AND u.public_id=:user"
                        ),
                        {"id": identity.session_id, "user": identity.user_id},
                    )
                )
                .mappings()
                .one()
            )
            assert row["status"] == "ACTIVE"
            await sql.execute(
                text("UPDATE user_session SET revoked_at=UTC_TIMESTAMP(6) WHERE id=:id"),
                {"id": identity.session_id},
            )
    finally:
        await engine.dispose()


def test_reusable_private_dual_socket_session_uses_production_jwt_and_sql_validation():
    if not os.getenv("JUYA_TEST_DATABASE_URL") or not os.getenv("JUYA_TEST_REDIS_URL"):
        pytest.skip("central UUID-isolated runner required")
    resources = PrivateResources.from_environment()
    with SocketTestKit(resources) as kit:
        assert all(kit.public_capabilities().values())
        with kit.client() as anonymous:
            assert anonymous.get("/api/v1/me").status_code == 401
        identity = kit.issue_session()
        with kit.client(identity) as client:
            me = client.get("/api/v1/me")
            assert me.status_code == 200 and me.json()["public_id"] == identity.user_id
            catalog = client.get("/api/v1/learning/catalog")
            assert catalog.status_code == 200 and not catalog.json()["authorization_pending"]
            assert catalog.json()["user_stage"] == "NEW"
            opened = client.post(
                f"/api/v1/scenes/{kit.scene_ids[0]}/open",
                headers={"Idempotency-Key": "testkit-open"},
            )
            assert opened.status_code == 200
            assert opened.json()["scene"]["revision_id"] == kit.revision_ids[0]
            assert opened.json()["scene"]["words"]
            assert client.get("/api/v1/favorite-snapshots", params={"limit": 20}).status_code == 200
            with pytest.raises(RuntimeError):
                client.get("https://external.invalid/")

        # A valid signature with an unrecorded sid must still fail the unchanged SQL session check.
        ghost_session = new_ulid(datetime.now(UTC))
        ghost = JwtService(kit._jwt_secret, kid="v15-isolated").issue_access_token(
            identity.user_id, ghost_session, datetime.now(UTC)
        )
        with kit.client(
            FixtureSession(identity.user_id, ghost_session, SecretStr(ghost))
        ) as client:
            assert client.get("/api/v1/me").status_code == 401
        token = identity.access_token.get_secret_value().split(".")
        token[2] = ("A" if token[2][0] != "A" else "B") + token[2][1:]
        with kit.client(
            FixtureSession(identity.user_id, identity.session_id, SecretStr(".".join(token)))
        ) as client:
            assert client.get("/api/v1/me").status_code == 401
        asyncio.run(revoke_owned_fixture(resources, identity))
        with kit.client(identity) as client:
            assert client.get("/api/v1/me").status_code == 401
            assert (
                client.post(
                    f"/api/v1/scenes/{kit.scene_ids[0]}/open",
                    headers={"Idempotency-Key": "revoked-open"},
                ).status_code
                == 401
            )
