import base64
import json
from datetime import UTC, datetime

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
