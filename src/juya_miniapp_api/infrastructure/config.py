from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JUYA_", extra="ignore")

    environment: str = "local"
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
    oss_bucket: str | None = None
    oss_access_key_id: SecretStr | None = None
    oss_access_key_secret: SecretStr | None = None

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
            self.oss_endpoint,
            self.oss_bucket,
            self.oss_access_key_id,
            self.oss_access_key_secret,
        )
        return all(
            bool(value.get_secret_value()) if isinstance(value, SecretStr) else bool(value)
            for value in values
        )
