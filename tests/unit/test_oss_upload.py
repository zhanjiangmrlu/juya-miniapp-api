import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from juya_miniapp_api.infrastructure.config import Settings
from juya_miniapp_api.integrations.oss.upload import OssUploadService
from juya_miniapp_api.main import create_app


def test_feedback_upload_has_v4_fields_bound_to_exact_key_and_mime() -> None:
    # 功能:验证反馈上传V4字段绑定精确对象键与MIME
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    # 匿名函数: clock测试时钟返回固定操作时间以稳定签名与有效期断言
    # 参数:
    #     无形参。
    # 返回: 测试预设的带UTC时区时间
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
    # 功能:验证生产启动拒绝缺失真实OSS配置
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    with pytest.raises(RuntimeError, match="OSS"):
        create_app(Settings(environment="production"))


def test_test_configuration_cannot_select_production_bucket() -> None:
    # 功能:验证测试环境不能选择生产存储桶
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    settings = Settings(
        environment="test",
        oss_bucket="juya",
        oss_region="cn-shenzhen",
        oss_expected_bucket="juya-test",
    )
    with pytest.raises(RuntimeError, match="bucket"):
        settings.validate_oss_configuration()


def test_empty_optional_sts_expiration_is_treated_as_unset(monkeypatch) -> None:
    # 功能:验证可选STS过期时间为空时视为未配置
    # 参数:
    #     monkeypatch: pytest提供的临时属性或环境变量替换工具
    # 返回:无返回值;断言失败时由pytest报告测试失败
    monkeypatch.setenv("JUYA_OSS_CREDENTIALS_EXPIRES_AT", "")
    assert Settings().oss_credentials_expires_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["JUYA_OSS_", "OSS_"])
async def test_runtime_rotates_complete_environment_sts_bundle(monkeypatch, prefix: str) -> None:
    # 功能:验证运行时完整轮换环境中的STS凭证组合
    # 参数:
    #     monkeypatch: pytest提供的临时属性或环境变量替换工具
    #     prefix: 测试上传对象所在的OSS目录前缀
    # 返回:无返回值;断言失败时由pytest报告测试失败
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
        # 功能:在测试中收集测试中产生的事件或调用信息供断言
        # 参数:
        #     kwargs: OSS测试调用中按SDK接口传入的关键字参数
        # 返回:用户图片直传凭证服务
        # 匿名函数: clock测试时钟返回固定操作时间以稳定签名与有效期断言
        # 参数:
        #     无形参。
        # 返回: 测试预设的带UTC时区时间
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
