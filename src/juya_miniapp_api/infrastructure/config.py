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
