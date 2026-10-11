from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from juya_miniapp_api.integrations.oss.avatar import AvatarStore
from juya_miniapp_api.modules.contacts.service import ContactService
from juya_miniapp_api.modules.users.repository import InMemoryUserRepository
from juya_miniapp_api.modules.users.router import create_users_router
from juya_miniapp_api.modules.users.service import UserService
from juya_miniapp_api.shared.errors import AppError, install_error_handlers

NOW = datetime(2026, 10, 11, tzinfo=UTC)
KEY = "avatars/owner/fixed.png"
URL = "https://private.example/avatars/owner/fixed.png?signature=test"


def profile_app():
    repository = InMemoryUserRepository()
    repository.add("owner", "JY100", nickname="old", avatar_object_key=KEY)
    contacts = Mock(spec=ContactService)
    contacts.get = AsyncMock(return_value=None)
    verifier = AsyncMock(return_value=KEY)
    signer = AsyncMock(return_value=URL)
    service = UserService(repository, contacts, avatar_verifier=verifier, avatar_url_signer=signer)

    async def owner():
        return "owner"

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(create_users_router(service, user_dependency=owner, clock=lambda: NOW))
    return TestClient(app), repository, verifier, signer


def test_me_returns_authoritative_signed_url_and_never_signs_foreign_or_upload_keys():
    client, repository, _, signer = profile_app()
    response = client.get("/api/v1/me")
    assert response.status_code == 200
    assert response.json()["avatar_url"] == URL
    assert response.headers["cache-control"] == "private, no-store"
    signer.assert_awaited_once_with("owner", KEY)
    for invalid in (None, "avatars/other/fixed.png", "uploads/avatars/owner/upload.png"):
        signer.reset_mock()
        repository.add("owner", "JY100", avatar_object_key=invalid)
        assert client.get("/api/v1/me").json()["avatar_url"] is None
        signer.assert_not_awaited()


def test_post_and_patch_share_profile_validation_persistence_and_signed_response():
    client, repository, verifier, signer = profile_app()
    for method in ("POST", "PATCH"):
        response = client.request(
            method,
            "/api/v1/me/profile",
            json={"nickname": "  saved  ", "avatar_object_key": "uploads/avatars/owner/new.png"},
        )
        assert response.status_code == 200
        assert response.json()["nickname"] == "saved"
        assert response.json()["avatar_object_key"] == KEY
        assert response.json()["avatar_url"] == URL
        assert repository.profiles["owner"].avatar_object_key == KEY
        assert response.headers["cache-control"] == "private, no-store"
        verifier.assert_awaited_with("owner", "uploads/avatars/owner/new.png")
        signer.assert_awaited_with("owner", KEY)
        denied = client.request(
            method,
            "/api/v1/me/profile",
            json={"nickname": "bad", "avatar_object_key": "avatars/other/fixed.png"},
        )
        assert denied.status_code == 422
        assert repository.profiles["owner"].nickname == "saved"


def test_profile_contract_documents_avatar_url_on_get_post_and_patch():
    client, _, _, _ = profile_app()
    spec = client.get("/openapi.json").json()
    for path, methods in (("/api/v1/me", ["get"]), ("/api/v1/me/profile", ["post", "patch"])):
        for method in methods:
            schema = spec["paths"][path][method]["responses"]["200"]["content"]["application/json"][
                "schema"
            ]
            model = spec["components"]["schemas"][schema["$ref"].split("/")[-1]]
            assert "avatar_url" in model["properties"]


def avatar_store(expiration=None, token=None):
    provider = Mock()
    provider.get_credentials.return_value = SimpleNamespace(
        expiration=expiration, security_token=token
    )
    store = AvatarStore(
        region="cn-hangzhou",
        bucket="private",
        endpoint=None,
        credentials_provider=provider,
        clock=lambda: NOW,
    )
    store.client = Mock()
    store.client.presign.return_value = SimpleNamespace(url=URL)
    return store


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key",
    [
        "avatars/other/fixed.png",
        "uploads/avatars/owner/new.png",
        "avatars/owner/../fixed.png",
        "https://example/avatar.png",
    ],
)
async def test_signer_rejects_unconfirmed_or_unowned_paths_before_sdk(key):
    store = avatar_store()
    with pytest.raises(AppError) as error:
        await store.sign_avatar_url("owner", key)
    assert error.value.code == "AVATAR_OBJECT_KEY_INVALID"
    store.client.presign.assert_not_called()


@pytest.mark.asyncio
async def test_signed_avatar_ttl_is_bounded_by_sts_lifetime_and_gets_private_object():
    store = avatar_store(NOW + timedelta(seconds=100), "temporary")
    assert await store.sign_avatar_url("owner", KEY) == URL
    args, kwargs = store.client.presign.call_args
    assert args[0].key == KEY and args[0].bucket == "private"
    assert kwargs["expires"] == timedelta(seconds=70)
    for expiry in (NOW, NOW + timedelta(seconds=30), NOW.replace(tzinfo=None), None):
        expired = avatar_store(expiry, "temporary")
        with pytest.raises(AppError) as error:
            await expired.sign_avatar_url("owner", KEY)
        assert error.value.status_code == 503
        expired.client.presign.assert_not_called()


@pytest.mark.asyncio
async def test_private_avatar_sdk_signs_locally_without_network():
    import alibabacloud_oss_v2 as oss

    provider = oss.credentials.StaticCredentialsProvider("test-access", "test-secret")
    store = AvatarStore(
        region="cn-hangzhou",
        bucket="test-private",
        endpoint=None,
        credentials_provider=provider,
    )
    url = await store.sign_avatar_url("owner", KEY)
    from urllib.parse import parse_qs, urlsplit

    parsed = urlsplit(url)
    assert parsed.scheme == "https" and parsed.path == "/" + KEY
    query = parse_qs(parsed.query)
    assert 0 < int(query["x-oss-expires"][0]) <= 300 and query["x-oss-signature"]
