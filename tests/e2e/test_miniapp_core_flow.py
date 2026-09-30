import base64

import pytest
from httpx import ASGITransport, AsyncClient

from juya_miniapp_api.infrastructure.config import Settings
from juya_miniapp_api.main import create_app


def runtime_settings() -> Settings:
    key = base64.urlsafe_b64encode(b"0" * 32).decode()
    return Settings(
        environment="test",
        database_url="mysql+asyncmy://user:pass@localhost/juya",
        redis_url="redis://localhost:6379/0",
        admin_api_base_url="http://admin.test",
        internal_hmac_secret="internal-secret",
        jwt_secret="jwt-secret",
        field_encryption_key_base64=key,
        field_lookup_key="lookup-secret",
        wechat_app_id="wx-test",
        wechat_app_secret="wechat-secret",
        oss_endpoint="oss-cn-test.aliyuncs.com",
        oss_bucket="juya-test",
        oss_region="cn-test",
        oss_expected_bucket="juya-test",
        oss_access_key_id="oss-key",
        oss_access_key_secret="oss-secret",
    )


def test_oss_upload_openapi_describes_v4_fields() -> None:
    app = create_app(runtime_settings())
    schema = app.openapi()
    response = schema["paths"]["/api/v1/feedback/uploads"]["post"]["responses"]["200"]
    reference = response["content"]["application/json"]["schema"]["$ref"]
    credential = schema["components"]["schemas"][reference.rsplit("/", 1)[-1]]
    assert credential["properties"]["fields"]["additionalProperties"]["type"] == "string"
    assert "fields" in credential["required"]


@pytest.mark.asyncio
async def test_runtime_exposes_the_complete_public_and_internal_route_manifest() -> None:
    app = create_app(runtime_settings())
    expected = {
        ("POST", "/api/v1/session/wechat"),
        ("GET", "/api/v1/home"),
        ("GET", "/api/v1/learning/catalog"),
        ("POST", "/api/v1/scenes/{scene_id}/open"),
        ("PUT", "/api/v1/scenes/{scene_id}/progress"),
        ("POST", "/api/v1/scenes/{scene_id}/complete"),
        ("POST", "/api/v1/favorites"),
        ("POST", "/api/v1/reviews"),
        ("PUT", "/api/v1/me/contact"),
        ("POST", "/api/v1/feedback"),
        ("GET", "/api/v1/messages"),
        ("DELETE", "/api/v1/me/learning-data"),
        ("POST", "/api/v1/me/deletion"),
        ("POST", "/api/v1/me/deletion/revoke"),
        ("POST", "/internal/v1/users/{user_id}/messages"),
        ("POST", "/internal/v1/users/{user_id}/deletion-cleanup-result"),
    }
    schema = app.openapi()
    actual = {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method in operations
    }

    assert expected <= actual

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/live")
    assert response.status_code == 200
