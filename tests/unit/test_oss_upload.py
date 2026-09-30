import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from juya_miniapp_api.infrastructure.config import Settings
from juya_miniapp_api.integrations.oss.upload import OssUploadService
from juya_miniapp_api.main import create_app


def test_feedback_upload_has_v4_fields_bound_to_exact_key_and_mime() -> None:
    service = OssUploadService(
        endpoint="https://oss-cn-shenzhen.aliyuncs.com",
        bucket="juya-test",
        region="cn-shenzhen",
        access_key_id="test-id",
        access_key_secret="test-secret",
        security_token="test-token",
        clock=lambda: datetime(2026, 9, 30, tzinfo=UTC),
    )
    upload = service.create_feedback_upload("user-1", "image/png")
    fields = upload["fields"]
    conditions = json.loads(base64.b64decode(fields["policy"]))["conditions"]
    assert fields["x-oss-signature-version"] == "OSS4-HMAC-SHA256"
    assert {"key": upload["key"]} in conditions
    assert {"Content-Type": "image/png"} in conditions
    assert {"x-oss-security-token": "test-token"} in conditions
    assert upload["host"] == "https://juya-test.oss-cn-shenzhen.aliyuncs.com"
    assert "test-secret" not in str(upload)


def test_production_startup_rejects_missing_real_oss_configuration() -> None:
    with pytest.raises(RuntimeError, match="OSS"):
        create_app(Settings(environment="production"))


def test_test_configuration_cannot_select_production_bucket() -> None:
    settings = Settings(
        environment="test",
        oss_bucket="juya",
        oss_region="cn-shenzhen",
        oss_expected_bucket="juya-test",
    )
    with pytest.raises(RuntimeError, match="bucket"):
        settings.validate_oss_configuration()


def test_empty_optional_sts_expiration_is_treated_as_unset(monkeypatch) -> None:
    monkeypatch.setenv("JUYA_OSS_CREDENTIALS_EXPIRES_AT", "")
    assert Settings().oss_credentials_expires_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["JUYA_OSS_", "OSS_"])
async def test_runtime_rotates_complete_environment_sts_bundle(monkeypatch, prefix: str) -> None:
    from fastapi import FastAPI

    from juya_miniapp_api.api import runtime as wiring

    now = datetime.now(UTC)
    for name in ("ACCESS_KEY_ID", "ACCESS_KEY_SECRET", "SESSION_TOKEN"):
        monkeypatch.delenv("JUYA_OSS_" + name, raising=False)
        monkeypatch.delenv("OSS_" + name, raising=False)
    monkeypatch.setenv(prefix + "ACCESS_KEY_ID", "old-id")
    monkeypatch.setenv(prefix + "ACCESS_KEY_SECRET", "old-secret")
    monkeypatch.setenv(prefix + "SESSION_TOKEN", "old-token")
    monkeypatch.setenv("JUYA_OSS_CREDENTIALS_EXPIRES_AT", (now + timedelta(seconds=90)).isoformat())
    captured: list[OssUploadService] = []

    def capture(**kwargs: Any) -> OssUploadService:
        service = OssUploadService(**kwargs, clock=lambda: now)
        captured.append(service)
        return service

    monkeypatch.setattr(wiring, "OssUploadService", capture)
    resources = wiring.install_application_routes(
        FastAPI(),
        Settings(
            environment="test",
            database_url="mysql+asyncmy://test:test@localhost/test",
            redis_url="redis://localhost:6399/0",
            internal_hmac_secret="x" * 32,
            field_encryption_key_base64=base64.urlsafe_b64encode(b"x" * 32).decode(),
            field_lookup_key="x" * 32,
            jwt_secret="x" * 32,
            wechat_app_id="test-app",
            wechat_app_secret="test-app-secret",
            oss_region="cn-shenzhen",
            oss_bucket="juya-test",
            oss_expected_bucket="juya-test",
        ),
    )
    try:
        first = captured[0].create_feedback_upload("user-1", "image/png")
        monkeypatch.setenv(prefix + "ACCESS_KEY_ID", "new-id")
        monkeypatch.setenv(prefix + "ACCESS_KEY_SECRET", "new-secret")
        monkeypatch.setenv(prefix + "SESSION_TOKEN", "new-token")
        monkeypatch.setenv(
            "JUYA_OSS_CREDENTIALS_EXPIRES_AT", (now + timedelta(seconds=900)).isoformat()
        )
        second = captured[0].create_feedback_upload("user-1", "image/png")
        assert first["expires_at"] == now + timedelta(seconds=60)
        assert second["fields"]["x-oss-credential"].startswith("new-id/")
        assert second["fields"]["x-oss-security-token"] == "new-token"
        assert second["expires_at"] == now + timedelta(seconds=300)
    finally:
        await resources.close()
