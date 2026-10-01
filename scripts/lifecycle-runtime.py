"""Explicit isolated fixtures: real HTTP/HMAC, Redis workers and test OSS lifecycle.

Run with the admin API virtualenv. Default only validates the running stack.
Never starts services, changes environment files or targets the business database.
"""

import argparse
import asyncio
import importlib.util
import io
import json
import secrets
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from celery import Celery
from PIL import Image
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "juya-admin-api/src"))
spec = importlib.util.spec_from_file_location(
    "worker_helper", Path(__file__).with_name("local-stack-worker.py")
)
assert spec and spec.loader
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

from juya_admin_api.infrastructure.config import Settings  # noqa: E402
from juya_admin_api.infrastructure.db.session import create_engine as async_engine  # noqa: E402
from juya_admin_api.infrastructure.db.session import create_session_factory  # noqa: E402
from juya_admin_api.infrastructure.security.service_hmac import sign_request  # noqa: E402
from juya_admin_api.integrations.oss.aliyun import AliyunOssProvider  # noqa: E402
from juya_admin_api.integrations.oss.credentials import ControlledCredentialsProvider  # noqa: E402
from juya_admin_api.modules.content.production_store import ProductionStore  # noqa: E402
from juya_admin_api.modules.content.repository import SQLAlchemyContentRepository  # noqa: E402
from juya_admin_api.modules.content.service import ContentService  # noqa: E402
from juya_admin_api.shared.ids import new_ulid  # noqa: E402


def signed_post(
    http: httpx.Client, base: str, path: str, payload: dict, secret: bytes
) -> httpx.Response:
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = int(time.time())
    nonce = secrets.token_hex(16)
    return http.post(
        base + path,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Juya-Service": "juya-admin-api",
            "X-Juya-Timestamp": str(timestamp),
            "X-Juya-Nonce": nonce,
            "X-Juya-Signature": sign_request("POST", path, timestamp, nonce, body, secret),
        },
    )


async def draft_fixtures(url: str, now: datetime) -> tuple[str, list[tuple[str, str, str]]]:
    engine = async_engine(url.replace("mysql+pymysql", "mysql+asyncmy"))
    sessions = create_session_factory(engine)
    store = ProductionStore(sessions, require_review=False)
    content = ContentService(SQLAlchemyContentRepository(sessions, require_review=False))
    series = await store.create_series("Synthetic lifecycle fixture", new_ulid(now), None)
    drafts = []
    try:
        for early in (False, True):
            scene = await store.create_scene(series["id"], "dialogue")
            revision = await content.create_revision(scene, None, "system", now)
            trash = new_ulid(now)
            drafts.append((scene, revision.id, trash))
            async with sessions() as session, session.begin():
                await session.execute(
                    text(
                        "INSERT INTO draft_trash(public_id,scene_public_id,revision_public_id,"
                        "status,trashed_by,trashed_at,retention_until) VALUES "
                        "(:id,:scene,:revision,'TRASHED','system',:now,:until)"
                    ),
                    {
                        "id": trash,
                        "scene": scene,
                        "revision": revision.id,
                        "now": now - timedelta(days=31),
                        "until": now + timedelta(days=1) if early else now - timedelta(days=1),
                    },
                )
        return series["id"], drafts
    finally:
        await engine.dispose()


async def oss_fixtures(config: dict, keys: list[str]) -> AliyunOssProvider:
    values = {
        key[5:].lower(): value
        for key, value in config.items()
        if key.startswith("JUYA_") and key[5:].lower() in Settings.model_fields
    }
    for suffix in ("ACCESS_KEY_ID", "ACCESS_KEY_SECRET", "SESSION_TOKEN"):
        value = config.get("JUYA_OSS_" + suffix) or config.get("OSS_" + suffix)
        if value:
            values["oss_" + suffix.lower()] = value
    settings = Settings(**values)
    settings.validate_oss_configuration()
    provider = AliyunOssProvider(
        settings.oss_region,
        settings.oss_bucket,
        endpoint=settings.oss_endpoint,
        credentials_provider=ControlledCredentialsProvider(
            mode=settings.oss_credentials_mode,
            access_key_id=settings.oss_access_key_id.get_secret_value(),
            access_key_secret=settings.oss_access_key_secret.get_secret_value(),
            security_token=settings.oss_session_token.get_secret_value()
            if settings.oss_session_token
            else None,
        ),
    )
    png = io.BytesIO()
    Image.new("RGB", (16, 16), (30, 90, 160)).save(png, format="PNG")
    async with httpx.AsyncClient(timeout=30) as http:
        for key in keys:
            policy = await provider.create_upload_policy(key.rsplit("/", 1)[0] + "/", 10000, 300)
            fields = dict(policy.fields, key=key)
            fields["Content-Type"] = "image/png"
            result = await http.post(
                policy.upload_url,
                data=fields,
                files={"file": ("synthetic.png", png.getvalue(), "image/png")},
            )
            assert result.status_code in {200, 201, 204}, (
                f"Synthetic OSS upload HTTP {result.status_code}"
            )
            assert (await provider.head_object(key)).size == len(png.getvalue())
    return provider


def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute", action="store_true", help="Create only isolated synthetic fixtures"
    )
    options = parser.parse_args()
    admin = helper.environment(helper.inspect_container("juya-v13-admin-api"))
    mini = helper.environment(helper.inspect_container("juya-v13-mini-api"))
    helper.validate_stack(admin, mini)
    for container in (
        "juya-v13-mini-worker",
        "juya-v13-worker-domain",
        "juya-v13-worker-content",
        "juya-v13-beat",
    ):
        info = helper.inspect_container(container)
        assert info["State"]["Running"], f"Required worker unavailable: {container}"
        worker = helper.environment(info)
        assert worker["JUYA_DATABASE_URL"].split("/")[-1] == "juya_v13_local_e2e"
        assert worker["JUYA_REDIS_URL"] == admin["JUYA_REDIS_URL"]
    report = {
        "database": "juya_v13_local_e2e",
        "redis_port": 6398,
        "scope": "self-created synthetic fixtures; test OSS; no cloud moderation acceptance",
        "mode": "executed" if options.execute else "read-only preflight",
        "checks": {},
    }
    if not options.execute:
        print(json.dumps(report))
        return
    url = (
        admin["JUYA_DATABASE_URL"]
        .replace("host.docker.internal", "127.0.0.1")
        .replace("mysql+asyncmy", "mysql+pymysql")
    )
    engine = create_engine(url)
    broker = admin["JUYA_REDIS_URL"].replace("host.docker.internal", "127.0.0.1")
    celery = Celery("isolated-lifecycle-evidence", broker=broker, backend=broker)
    celery.conf.update(task_serializer="json", result_serializer="json", accept_content=["json"])
    now = datetime.now(UTC)
    user, request, ticket, other, metric_event = [new_ulid(now) for _ in range(5)]
    keys = [
        f"feedback/{ticket}/fixtures/lifecycle/synthetic.png",
        f"feedback/{other}/fixtures/lifecycle/synthetic.png",
    ]
    series = None
    drafts = []
    oss = None
    checks = report["checks"]

    def task(name: str, queue: str):
        result = celery.send_task(name, queue=queue).get(timeout=30)
        if name in checks:
            previous = checks[name]
            checks[name] = [*previous, result] if isinstance(previous, list) else [previous, result]
        else:
            checks[name] = result
        return result

    try:
        # Refuse a global task if it could process another fixture's destructive work.
        with engine.connect() as connection:
            for query in (
                "SELECT COUNT(*) FROM account_deletion_request "
                "WHERE status IN ('PENDING','DELETING') AND effective_at<=UTC_TIMESTAMP(6)",
                "SELECT COUNT(*) FROM feedback_screenshot "
                "WHERE deleted_at IS NULL AND delete_after<=UTC_TIMESTAMP(6)",
                "SELECT COUNT(*) FROM draft_trash "
                "WHERE status='TRASHED' AND retention_until<=UTC_TIMESTAMP(6)",
            ):
                assert connection.scalar(text(query)) == 0, (
                    "Other due fixtures exist; coordinate before running"
                )
        oss = asyncio.run(oss_fixtures(admin, keys))
        series, drafts = asyncio.run(draft_fixtures(url, now))
        metric_dimension = "scene:" + drafts[0][0]
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO user_account(public_id,juya_number,status) VALUES(:id,"
                    ":number,'DELETION_PENDING')"
                ),
                {"id": user, "number": "JY" + user[-12:]},
            )
            pk = connection.scalar(text("SELECT LAST_INSERT_ID()"))
            connection.execute(
                text(
                    "INSERT INTO account_deletion_request(public_id,user_id,requested_at,"
                    "effective_at,status) VALUES(:id,:user,:past,:now,'PENDING')"
                ),
                {
                    "id": request,
                    "user": pk,
                    "past": now - timedelta(days=8),
                    "now": now - timedelta(minutes=1),
                },
            )
            for public_id, owner, key in ((ticket, pk, keys[0]), (other, None, keys[1])):
                connection.execute(
                    text(
                        "INSERT INTO feedback_ticket(public_id,user_id,category,description,"
                        "source,status,sla_hours,create_idempotency_key,created_at,"
                        "updated_at) VALUES(:id,:user,'FUNCTION','Synthetic lifecycle "
                        "fixture',JSON_OBJECT(),:status,48,:id,:now,:now)"
                    ),
                    {
                        "id": public_id,
                        "user": owner,
                        "now": now,
                        "status": "RESOLVED" if owner else "PROCESSING",
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO feedback_screenshot(ticket_id,object_key,security_status,"
                        "delete_after) SELECT id,:key,'PASSED',:due FROM feedback_ticket "
                        "WHERE public_id=:id"
                    ),
                    {
                        "id": public_id,
                        "key": key,
                        "due": now + timedelta(days=30) if owner else now - timedelta(days=1),
                    },
                )
            connection.execute(
                text(
                    "INSERT INTO analytics_event(id,event_key,user_id,event_type,occurred_at,"
                    "dimension,payload) VALUES(:id,:id,:user,'USER_ACTIVE',:past,:dimension,"
                    "JSON_OBJECT())"
                ),
                {
                    "id": metric_event,
                    "user": pk,
                    "past": now - timedelta(days=1),
                    "dimension": metric_dimension,
                },
            )
        with httpx.Client(timeout=15) as http:
            path = f"/internal/v1/users/{user}/deletion-cleanup-result"
            body = {"deletion_request_id": request, "succeeded": True}
            assert http.post("http://127.0.0.1:18001" + path, json=body).status_code == 401
            assert (
                signed_post(http, "http://127.0.0.1:18001", path, body, b"wrong-secret").status_code
                == 401
            )
            checks["unsigned_and_wrong_hmac_rejected"] = True
        task("juya.accounts.execute_due", "miniapp.account")
        task("juya.accounts.dispatch_outbox", "miniapp.account")
        # Make only this fixture's consumer reject one signed request, then prove durable retry.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE admin_outbox SET payload=JSON_SET(payload,'$.deletion_request_id',"
                    ":wrong) WHERE aggregate_public_id=:id AND "
                    "event_type='DELETION_CLEANUP_RESULT'"
                ),
                {"id": request, "wrong": new_ulid(now)},
            )
        assert task("juya.domain.messages.dispatch_outbox", "domain.messages") == {
            "delivered": 0,
            "failed": 1,
        }
        with engine.connect() as connection:
            retry = connection.execute(
                text(
                    "SELECT status,attempt_count,next_attempt_at FROM admin_outbox WHERE "
                    "aggregate_public_id=:id AND event_type='DELETION_CLEANUP_RESULT'"
                ),
                {"id": request},
            ).one()
            assert retry.status == "PENDING" and retry.attempt_count == 1
            assert retry.next_attempt_at > datetime.now(UTC).replace(tzinfo=None) + timedelta(
                seconds=40
            )
            assert (
                connection.scalar(
                    text("SELECT status FROM user_account WHERE public_id=:id"), {"id": user}
                )
                == "DELETING"
            )
        assert task("juya.content.assets.cleanup_feedback_screenshots", "content.assets") == {
            "deleted": 0,
            "failed": 0,
        }
        assert asyncio.run(oss.head_object(keys[0])).size > 0
        checks["failed_signed_callback_backoff_keeps_resolved_screenshot"] = True
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE admin_outbox SET payload=JSON_SET(payload,'$.deletion_request_id',"
                    ":id),next_attempt_at=:due WHERE aggregate_public_id=:id AND "
                    "event_type='DELETION_CLEANUP_RESULT'"
                ),
                {"id": request, "due": now - timedelta(minutes=1)},
            )
        task("juya.domain.messages.dispatch_outbox", "domain.messages")
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT status FROM account_deletion_request WHERE public_id=:id"),
                    {"id": request},
                )
                == "DELETED"
            )
            assert (
                connection.scalar(
                    text("SELECT status FROM miniapp_outbox WHERE aggregate_id=:id"),
                    {"id": request},
                )
                == "DELIVERED"
            )
            callback = connection.execute(
                text(
                    "SELECT event_id,status FROM admin_outbox WHERE aggregate_public_id=:id "
                    "AND event_type='DELETION_CLEANUP_RESULT'"
                ),
                {"id": request},
            ).one()
            assert callback.status == "PUBLISHED"
            assert (
                connection.scalar(
                    text("SELECT user_id FROM analytics_event WHERE id=:id"), {"id": metric_event}
                )
                is None
            )
            checks["terminal_deletion_and_anonymous_event"] = True
        with httpx.Client(timeout=15) as http:
            replay = signed_post(
                http,
                "http://127.0.0.1:18001",
                path,
                body,
                admin["JUYA_INTERNAL_HMAC_SECRET"].encode(),
            )
            assert replay.status_code == 200
            checks["signed_callback_replay_idempotent"] = True
        task("juya.content.assets.cleanup_feedback_screenshots", "content.assets")
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT deleted_at FROM feedback_screenshot WHERE object_key=:key"),
                    {"key": keys[0]},
                )
                is not None
            )
            assert (
                connection.scalar(
                    text("SELECT deleted_at FROM feedback_screenshot WHERE object_key=:key"),
                    {"key": keys[1]},
                )
                is None
            )

        async def inspect_oss():
            from juya_admin_api.shared.errors import AppError

            try:
                await oss.head_object(keys[0])
            except AppError as error:
                assert error.code == "OSS_OBJECT_NOT_FOUND"
            else:
                raise AssertionError("Completed deletion screenshot still exists")
            assert (await oss.head_object(keys[1])).size > 0

        asyncio.run(inspect_oss())
        checks["test_oss_deleted_only_completed_user_screenshot"] = True
        assert task("juya.content.assets.cleanup_expired_drafts", "content.assets") == {
            "cleaned": 1,
            "protected": 0,
        }
        assert task("juya.content.assets.cleanup_expired_drafts", "content.assets") == {
            "cleaned": 0,
            "protected": 0,
        }
        with engine.connect() as connection:
            states = [
                connection.scalar(
                    text("SELECT status FROM draft_trash WHERE public_id=:id"), {"id": entry[2]}
                )
                for entry in drafts
            ]
            assert states == ["CLEANED", "TRASHED"]
        checks["expired_draft_and_redelivery_preserve_early_draft"] = True
        task("juya.content.analytics.aggregate_daily", "content.analytics")
        with engine.connect() as connection:
            value = connection.scalar(
                text(
                    "SELECT metric_value FROM analytics_daily WHERE metric='ACTIVE_USERS' AND "
                    "dimension=:dimension ORDER BY metric_day DESC LIMIT 1"
                ),
                {"dimension": metric_dimension},
            )
            assert value == 1
        checks["real_worker_aggregated_anonymous_ended_day_event"] = True
        report["status"] = "PASSED"
    finally:
        # Delete only this run's explicitly named fixtures. Anonymous event/aggregate survives.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE e FROM deletion_cleanup_event e JOIN admin_outbox a ON BINARY "
                    "a.event_id=BINARY e.event_id WHERE a.aggregate_public_id=:id"
                ),
                {"id": request},
            )
            connection.execute(
                text("DELETE FROM admin_outbox WHERE aggregate_public_id=:id"), {"id": request}
            )
            connection.execute(
                text("DELETE FROM miniapp_outbox WHERE aggregate_id=:id"), {"id": request}
            )
            for public_id in (ticket, other):
                connection.execute(
                    text("DELETE FROM feedback_ticket WHERE public_id=:id"), {"id": public_id}
                )
                connection.execute(
                    text("DELETE FROM audit_event WHERE object_public_id=:id"), {"id": public_id}
                )
            connection.execute(
                text("DELETE FROM account_deletion_request WHERE public_id=:id"), {"id": request}
            )
            connection.execute(text("DELETE FROM user_account WHERE public_id=:id"), {"id": user})
            for scene, revision, trash in drafts:
                connection.execute(
                    text("DELETE FROM draft_trash WHERE public_id=:id"), {"id": trash}
                )
                connection.execute(
                    text("UPDATE scene SET draft_revision_id=NULL WHERE public_id=:id"),
                    {"id": scene},
                )
                connection.execute(
                    text("DELETE FROM scene_revision WHERE public_id=:id"), {"id": revision}
                )
                connection.execute(text("DELETE FROM scene WHERE public_id=:id"), {"id": scene})
                connection.execute(
                    text("DELETE FROM audit_event WHERE object_public_id=:id"), {"id": trash}
                )
            if series:
                connection.execute(
                    text("DELETE FROM content_series WHERE public_id=:id"), {"id": series}
                )
        if oss:
            for key in keys:
                asyncio.run(oss.delete_object(key))
        engine.dispose()
        celery.close()
        target = ROOT / "juya-miniapp-api/docs/closing/runtime-lifecycle-evidence.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    run()
