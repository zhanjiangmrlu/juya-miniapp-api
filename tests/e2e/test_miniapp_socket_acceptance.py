"""Real loopback HTTP, JWT, HMAC, MySQL and Redis; no business database or cloud calls."""

import asyncio
import base64
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from juya_miniapp_api.api.local_content import published_scene
from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory
from juya_miniapp_api.shared.ids import new_ulid

ROOT = Path(__file__).parents[2]
ADMIN = ROOT.parent / "juya-admin-api"


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def admin_fixture_state(database: str, ticket: str, status: str, round_no: int = 0) -> None:
    """Arrange only the newly created acceptance ticket's administrator transition."""
    engine = create_engine(database.replace("mysql+pymysql://", "mysql+asyncmy://"))
    try:
        async with create_session_factory(engine)() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE feedback_ticket SET status=:status,supplement_rounds=:round,"
                    "sla_remaining_seconds=3600,resolved_at=UTC_TIMESTAMP(6) WHERE public_id=:id"
                ),
                {"status": status, "round": round_no, "id": ticket},
            )
            if status == "NEED_MORE":
                await session.execute(
                    text(
                        "INSERT INTO feedback_round(ticket_id,round_number,request_text,paused_at) "
                        "SELECT id,:round,'Provide steps',UTC_TIMESTAMP(6) FROM feedback_ticket "
                        "WHERE public_id=:id"
                    ),
                    {"round": round_no, "id": ticket},
                )
    finally:
        await engine.dispose()


async def seed_contract(database: str) -> tuple[list[str], list[str], dict, str]:
    engine = create_engine(database.replace("mysql+pymysql://", "mysql+asyncmy://"))
    sessions = create_session_factory(engine)
    now = datetime.now(UTC)
    series, image = new_ulid(now), new_ulid(now)
    scene_ids = [new_ulid(now) for _ in range(3)]
    revisions = [new_ulid(now) for _ in range(3)]
    content = published_scene("scene-coffee-shop", {"scene": {}})["scene"]["content"]
    content["original_image_asset_id"] = image
    content["cover_asset_id"] = image
    content["audio"] = None
    for entry in [*content["vocabulary"], *content["chunks"]]:
        entry["audio_target_id"] = None
        entry["audio_version_id"] = None
    async with sessions() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO content_series(public_id,slug,title,status) "
                "VALUES(:id,:id,'Acceptance','PUBLISHED')"
            ),
            {"id": series},
        )
        series_pk = await session.scalar(text("SELECT LAST_INSERT_ID()"))
        await session.execute(
            text(
                "INSERT IGNORE INTO content_template(template_type,version,"
                "required_modules,validation_rules) "
                "VALUES('socket-acceptance',1,JSON_ARRAY(),JSON_OBJECT())"
            )
        )
        template = await session.scalar(
            text("SELECT id FROM content_template WHERE template_type='socket-acceptance'")
        )
        await session.execute(
            text(
                "INSERT INTO media_asset(public_id,object_key,asset_type,content_type,"
                "size_bytes,sha256,status,security_status,created_by,created_at) "
                "VALUES(:id,:key,'images','image/png',1,:hash,'CONFIRMED','SKIPPED','acceptance',:now)"
            ),
            {
                "id": image,
                "key": f"sealed/media/{image}.png",
                "hash": secrets.token_hex(32),
                "now": now,
            },
        )
        version = int(
            await session.scalar(text("SELECT COALESCE(MAX(version),0)+1 FROM open_scene_config"))
        )
        await session.execute(
            text(
                "INSERT INTO open_scene_config(version,activated_at,actor_public_id) "
                "VALUES(:version,:now,'acceptance')"
            ),
            {"version": version, "now": now},
        )
        config = await session.scalar(text("SELECT LAST_INSERT_ID()"))
        for index, (scene, revision) in enumerate(zip(scene_ids, revisions, strict=True)):
            await session.execute(
                text(
                    "INSERT INTO scene(public_id,series_id,template_id,title,status) "
                    "VALUES(:id,:series,:template,'Coffee','PUBLISHED')"
                ),
                {"id": scene, "series": series_pk, "template": template},
            )
            scene_pk = await session.scalar(text("SELECT LAST_INSERT_ID()"))
            await session.execute(
                text(
                    "INSERT INTO scene_revision(public_id,scene_id,version_no,status,"
                    "content_snapshot,created_by,published_at) "
                    "VALUES(:id,:scene,1,'PUBLISHED',:content,'acceptance',:now)"
                ),
                {"id": revision, "scene": scene_pk, "content": json.dumps(content), "now": now},
            )
            revision_pk = await session.scalar(text("SELECT LAST_INSERT_ID()"))
            await session.execute(
                text("UPDATE scene SET published_revision_id=:revision WHERE id=:id"),
                {"revision": revision_pk, "id": scene_pk},
            )
            await session.execute(
                text(
                    "INSERT INTO open_scene_item(config_id,scene_id,position) "
                    "VALUES(:config,:scene,:position)"
                ),
                {"config": config, "scene": scene_pk, "position": index + 1},
            )
    await engine.dispose()
    return scene_ids, revisions, content, image


def test_normal_runtime_over_socket_closes_miniapp_user_flows(tmp_path: Path) -> None:
    database = os.getenv("JUYA_TEST_DATABASE_URL")
    redis = os.getenv("JUYA_TEST_REDIS_URL")
    isolated = os.getenv("JUYA_V13_ISOLATED_DATABASE", "")
    if not database or not redis or not isolated.startswith("juya_v13_"):
        pytest.skip("UUID-isolated MySQL and Redis runner required")
    # The runner drops its own UUID database after this test; no shared stack is modified
    scene_ids, revisions, content, image = asyncio.run(seed_contract(database))
    now = datetime.now(UTC)
    admin_port, mini_port = free_port(), free_port()
    secret = secrets.token_urlsafe(32)
    env = {
        **os.environ,
        "JUYA_ENVIRONMENT": "test",
        "JUYA_LOCAL_DEV_MODE": "false",
        "JUYA_DATABASE_URL": database,
        "JUYA_REDIS_URL": redis,
        "JUYA_INTERNAL_HMAC_SECRET": secret,
        "JUYA_CONTENT_SECURITY_ENABLED": "false",
        "JUYA_OSS_REGION": "cn-test",
        "JUYA_OSS_BUCKET": "acceptance-local",
        "JUYA_OSS_EXPECTED_BUCKET": "acceptance-local",
        "OSS_ACCESS_KEY_ID": "acceptance-placeholder",
        "OSS_ACCESS_KEY_SECRET": "acceptance-placeholder",
        "JUYA_JWT_SECRET": secrets.token_urlsafe(32),
        "JUYA_FIELD_ENCRYPTION_KEY_BASE64": base64.urlsafe_b64encode(
            secrets.token_bytes(32)
        ).decode(),
        "JUYA_FIELD_LOOKUP_KEY": secrets.token_urlsafe(32),
        "JUYA_WECHAT_APP_ID": "wx-acceptance",
        "JUYA_WECHAT_APP_SECRET": "provider-fixture",
        "JUYA_ADMIN_API_BASE_URL": f"http://127.0.0.1:{admin_port}",
        "JUYA_MINIAPP_API_BASE_URL": f"http://127.0.0.1:{mini_port}",
    }
    env["JUYA_DATABASE_URL"] = database.replace("mysql+pymysql://", "mysql+asyncmy://")
    processes = []
    logs = []
    try:
        for name, python, repo, app, port in (
            (
                "admin",
                str(ADMIN / ".venv/Scripts/python.exe"),
                ADMIN,
                "juya_admin_api.main:app",
                admin_port,
            ),
            ("mini", sys.executable, ROOT, "socket_runtime_app:app", mini_port),
        ):
            log = (tmp_path / f"{name}.log").open("wb")
            logs.append(log)
            child_env = {
                **env,
                "PYTHONPATH": os.pathsep.join([str(repo / "src"), str(ROOT / "tests")]),
            }
            processes.append(
                subprocess.Popen(
                    [python, "-m", "uvicorn", app, "--host", "127.0.0.1", "--port", str(port)],
                    cwd=repo,
                    env=child_env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            )
        with httpx.Client(base_url=f"http://127.0.0.1:{mini_port}", timeout=10) as client:
            for port in (admin_port, mini_port):
                deadline = time.monotonic() + 30
                while True:
                    try:
                        if client.get(f"http://127.0.0.1:{port}/health/ready").status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    assert time.monotonic() < deadline, "isolated runtime did not start"
                    time.sleep(0.1)
            assert client.get("/api/v1/me").status_code == 401
            login = client.post(
                "/api/v1/session/wechat",
                json={"code": "acceptance-" + new_ulid(now), "device": "socket"},
            )
            assert login.status_code == 200, login.text
            user = login.json()["user"]["public_id"]
            client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
            me = client.get("/api/v1/me")
            assert me.status_code == 200 and not me.json()["contact_prompt_eligible"]
            assert client.get("/api/v1/home").status_code == 200
            catalog = client.get("/api/v1/learning/catalog").json()
            assert not catalog["authorization_pending"]
            assert (
                next(row for row in catalog["items"] if row["scene_id"] == scene_ids[0])[
                    "trial_sentence"
                ]
                == content["dialogue"][0]["english"]
            )
            rights = client.get("/api/v1/me/entitlements")
            assert rights.status_code == 200 and not rights.json()["authorization_pending"]
            scene, revision = scene_ids[0], revisions[0]
            opened = client.post(
                f"/api/v1/scenes/{scene}/open", headers={"Idempotency-Key": "open"}
            )
            assert opened.status_code == 200 and opened.json()["scene"]["revision_id"] == revision
            locator = "sentence:sentence-1:entry:word-latte"
            params = {"revision_id": revision, "entry_version": 1, "source_locator": locator}
            entry = client.get(f"/api/v1/scenes/{scene}/entries/word-latte", params=params)
            assert entry.status_code == 200 and entry.json()["english"] == "latte"
            resource = client.get(
                f"/api/v1/scenes/{scene}/resources/{image}/signed-url",
                params={"revision_id": revision},
            )
            assert resource.status_code == 200 and "expires_at" in resource.json()
            assert (
                client.get(
                    f"/api/v1/scenes/{scene}/resources/{image}/signed-url",
                    params={"revision_id": revisions[1]},
                ).status_code
                == 409
            )
            favorite = client.post(
                "/api/v1/favorites",
                json={
                    "entry_type": "VOCABULARY",
                    "text": "latte",
                    "entry_stable_id": "word-latte",
                    "scene_id": scene,
                    "sentence_snapshot": "ignored",
                    "source_locator": locator,
                    "revision_id": revision,
                    "entry_version": 1,
                },
            )
            assert favorite.status_code == 201, favorite.text
            assert favorite.json()["sources"][0]["entry_snapshot"]["chinese"] == "拿铁咖啡"
            assert client.get("/api/v1/favorites").json()["items"]
            for item in scene_ids:
                completed = client.post(
                    f"/api/v1/scenes/{item}/complete",
                    headers={"Idempotency-Key": "complete-" + item},
                )
                assert completed.status_code == 200, completed.text
            assert client.get("/api/v1/me").json()["contact_prompt_eligible"]
            for key, expected in (("device-a", True), ("device-b", False)):
                response = client.post(
                    "/api/v1/me/contact/prompt-exposures", headers={"Idempotency-Key": key}
                )
                assert response.json()["created"] is expected
            assert not client.get("/api/v1/me").json()["contact_prompt_eligible"]
            result = client.get(f"/api/v1/scenes/{scene}/result").json()
            assert result["completed_scenes"] == 3 and result["favorite_vocabulary"] == 1
            saved = client.put(
                "/api/v1/me/contact",
                json={
                    "wechat_id": "acceptance_user",
                    "consent_version": "v1",
                    "consent_confirmed": True,
                },
            )
            assert saved.status_code == 200, saved.text
            assert client.get("/api/v1/me/contact").json()["wechat_id"] == "acceptance_user"
            feedback = client.post(
                "/api/v1/feedback",
                headers={"Idempotency-Key": "feedback-create"},
                json={
                    "category": "CONTENT",
                    "description": "Acceptance feedback",
                    "source": {},
                    "screenshots": [],
                },
            )
            assert feedback.status_code == 200, feedback.text
            ticket = feedback.json()["id"]
            assert client.get("/api/v1/feedback").json()["items"][0]["id"] == ticket
            assert client.get(f"/api/v1/feedback/{ticket}").status_code == 200
            asyncio.run(admin_fixture_state(database, ticket, "NEED_MORE", 1))
            supplied = client.post(
                f"/api/v1/feedback/{ticket}/supplements",
                headers={"Idempotency-Key": "supplement-one"},
                json={"text": "Reproduction steps", "screenshots": [f"feedback/{user}/test.png"]},
            )
            assert supplied.status_code == 200 and supplied.json()["status"] == "USER_SUPPLIED"
            detail = client.get(f"/api/v1/feedback/{ticket}").json()
            assert detail["supplements"][0]["text"] == "Reproduction steps"
            assert detail["screenshots"] == [f"feedback/{user}/test.png"]
            asyncio.run(admin_fixture_state(database, ticket, "NEED_MORE", 2))
            duplicate = client.post(
                f"/api/v1/feedback/{ticket}/supplements",
                headers={"Idempotency-Key": "supplement-two-image"},
                json={"text": "Further details", "screenshots": [f"feedback/{user}/second.png"]},
            )
            assert duplicate.status_code == 422
            assert duplicate.json()["code"] == "FEEDBACK_SCREENSHOT_LIMIT"
            assert client.get(f"/api/v1/feedback/{ticket}").json()["status"] == "NEED_MORE"
            assert (
                client.post(
                    f"/api/v1/feedback/{ticket}/supplements",
                    headers={"Idempotency-Key": "supplement-two"},
                    json={"text": "Further details"},
                ).status_code
                == 200
            )
            asyncio.run(admin_fixture_state(database, ticket, "RESOLVED", 2))
            reopened = client.post(
                f"/api/v1/feedback/{ticket}/resolution",
                headers={"Idempotency-Key": "reopen"},
                json={"action": "REOPEN", "reason": "Still wrong"},
            )
            assert reopened.status_code == 200 and reopened.json()["reopen_count"] == 1
            assert client.get("/api/v1/messages").status_code == 200
            assert (
                client.request(
                    "DELETE",
                    "/api/v1/me/learning-data",
                    json={"confirmation": "CLEAR_LEARNING_DATA"},
                ).status_code
                == 204
            )
            assert client.get("/api/v1/favorites").json()["items"] == []
            deletion = client.post("/api/v1/me/deletion")
            assert deletion.status_code == 202, deletion.text
            assert client.get("/api/v1/me").json()["deletion"]["status"] == "PENDING"
            revoked = client.post("/api/v1/me/deletion/revoke")
            assert revoked.status_code == 200 and revoked.json()["status"] == "REVOKED"
            assert client.get("/api/v1/me").json()["deletion"] is None
            assert user
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for log in logs:
            log.close()
