from datetime import UTC, datetime, timedelta

import pytest

from juya_miniapp_api.infrastructure.security.field_cipher import FieldCipher
from juya_miniapp_api.infrastructure.security.jwt_service import JwtService
from juya_miniapp_api.integrations.wechat.protocol import WechatIdentity
from juya_miniapp_api.modules.auth.repository import InMemoryAuthRepository
from juya_miniapp_api.modules.auth.service import SessionService
from juya_miniapp_api.shared.errors import AppError

NOW = datetime(2026, 9, 28, 18, 0, tzinfo=UTC)


class FakeWechatProvider:
    async def exchange_code(self, code: str) -> WechatIdentity:
        return WechatIdentity(app_id="wx-app", openid=f"openid-{code}")


def make_service() -> tuple[SessionService, InMemoryAuthRepository, JwtService]:
    repository = InMemoryAuthRepository()
    jwt = JwtService(b"j" * 32, kid="miniapp-key-1")
    return (
        SessionService(
            FakeWechatProvider(),
            repository,
            FieldCipher(b"k" * 32, b"h" * 32),
            jwt,
        ),
        repository,
        jwt,
    )


@pytest.mark.asyncio
async def test_access_claims_and_refresh_lifetimes() -> None:
    service, _repository, jwt = make_service()

    session = await service.login_with_wechat("code-1", "iphone", NOW)
    claims = jwt.decode_access_token(session.access_token, now=NOW)

    assert claims["aud"] == "miniapp"
    assert claims["sub"] == session.user.public_id
    assert claims["sid"] == session.session_id
    assert claims["kid"] == "miniapp-key-1"
    assert claims["exp"] - claims["iat"] == 2 * 60 * 60
    assert session.refresh_expires_at == NOW + timedelta(days=30)


@pytest.mark.asyncio
async def test_refresh_rotates_and_replay_revokes_session_family() -> None:
    service, _repository, _jwt = make_service()
    session = await service.login_with_wechat("code-1", "iphone", NOW)

    rotated = await service.refresh(session.refresh_token, NOW + timedelta(minutes=1))
    assert rotated.refresh_token != session.refresh_token
    assert rotated.session_id == session.session_id

    with pytest.raises(AppError) as replay:
        await service.refresh(session.refresh_token, NOW + timedelta(minutes=2))
    assert replay.value.code == "REFRESH_TOKEN_REPLAYED"

    with pytest.raises(AppError) as revoked:
        await service.refresh(rotated.refresh_token, NOW + timedelta(minutes=3))
    assert revoked.value.code == "SESSION_REVOKED"


@pytest.mark.asyncio
async def test_logout_and_revoke_all_invalidate_refresh_tokens() -> None:
    service, _repository, _jwt = make_service()
    first = await service.login_with_wechat("code-1", "iphone", NOW)
    second = await service.login_with_wechat("code-1", "ipad", NOW)

    await service.logout(first.session_id, NOW)
    with pytest.raises(AppError) as logged_out:
        await service.refresh(first.refresh_token, NOW)
    assert logged_out.value.code == "SESSION_REVOKED"

    await service.revoke_all(second.user.public_id, "account deletion", NOW)
    with pytest.raises(AppError) as revoked:
        await service.refresh(second.refresh_token, NOW)
    assert revoked.value.code == "SESSION_REVOKED"


@pytest.mark.asyncio
async def test_deletion_pending_status_is_returned_without_identity_data() -> None:
    service, repository, _jwt = make_service()
    repository.next_user_status = "DELETION_PENDING"

    session = await service.login_with_wechat("code-1", "iphone", NOW)

    assert session.user.status == "DELETION_PENDING"
    assert session.account_summary == {"deletion_pending": True}
    assert "openid" not in repr(session)


@pytest.mark.asyncio
async def test_deleting_account_cannot_create_a_new_session() -> None:
    service, repository, _jwt = make_service()
    repository.next_user_status = "DELETING"

    with pytest.raises(AppError) as blocked:
        await service.login_with_wechat("code-1", "iphone", NOW)

    assert blocked.value.code == "ACCOUNT_DELETING"
    assert not repository.sessions
