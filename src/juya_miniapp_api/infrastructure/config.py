import os
from datetime import UTC, datetime
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JUYA_", extra="ignore")

    environment: str = "local"
    local_dev_mode: bool = False
    service_name: str = "juya-miniapp-api"
    log_level: str = "INFO"
    database_url: SecretStr | None = None
    redis_url: SecretStr | None = None
    admin_api_base_url: str = "http://juya-admin-api:8000"
    internal_hmac_secret: SecretStr | None = None
    jwt_secret: SecretStr | None = None
    jwt_key_id: str = "v1"
    field_encryption_key_base64: SecretStr | None = None
    field_lookup_key: SecretStr | None = None
    wechat_app_id: str | None = None
    wechat_app_secret: SecretStr | None = None
    oss_endpoint: str | None = None
    oss_region: str | None = None
    oss_bucket: str | None = None
    oss_expected_bucket: str | None = None
    oss_credentials_mode: Literal["environment", "ecs_ram_role"] = "environment"
    oss_ram_role_name: str | None = None
    oss_access_key_id: SecretStr | None = None
    oss_access_key_secret: SecretStr | None = None
    oss_session_token: SecretStr | None = None
    oss_credentials_expires_at: datetime | None = None

    def validate_oss_configuration(self) -> None:
        if self.oss_expected_bucket and self.oss_bucket != self.oss_expected_bucket:
            raise RuntimeError("OSS bucket does not match the expected environment bucket")
        if not self.oss_region or not self.oss_bucket or not self.oss_expected_bucket:
            raise RuntimeError("OSS requires JUYA_OSS_REGION, BUCKET and EXPECTED_BUCKET")
        if self.oss_credentials_mode == "ecs_ram_role":
            if not self.oss_ram_role_name:
                raise RuntimeError("OSS requires JUYA_OSS_RAM_ROLE_NAME")
            return
        if not (self.oss_access_key_id or os.getenv("OSS_ACCESS_KEY_ID")) or not (
            self.oss_access_key_secret or os.getenv("OSS_ACCESS_KEY_SECRET")
        ):
            raise RuntimeError("OSS server credentials are required")
        if self.oss_session_token or os.getenv("OSS_SESSION_TOKEN"):
            expiry = self.oss_credentials_expires_at
            if expiry is None or expiry.tzinfo is None or expiry <= datetime.now(UTC):
                raise RuntimeError("OSS STS credentials require a future UTC expiration")

    def application_configured(self) -> bool:
        values = (
            self.database_url,
            self.redis_url,
            self.internal_hmac_secret,
            self.jwt_secret,
            self.field_encryption_key_base64,
            self.field_lookup_key,
            self.wechat_app_id,
            self.wechat_app_secret,
            self.oss_bucket,
        )
        configured = all(
            bool(value.get_secret_value()) if isinstance(value, SecretStr) else bool(value)
            for value in values
        )
        try:
            self.validate_oss_configuration()
        except RuntimeError:
            return False
        return configured
