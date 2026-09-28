import hashlib
import secrets
from datetime import datetime, timedelta

from juya_miniapp_api.infrastructure.security.field_cipher import FieldCipher
from juya_miniapp_api.infrastructure.security.jwt_service import JwtService
from juya_miniapp_api.integrations.wechat.protocol import WechatAuthProvider
from juya_miniapp_api.modules.auth.domain import SessionRecord, SessionTokens
from juya_miniapp_api.modules.auth.repository import AuthRepository
from juya_miniapp_api.modules.users.models import UserSummary
from juya_miniapp_api.shared.errors import AppError
from juya_miniapp_api.shared.ids import new_ulid

REFRESH_TOKEN_LIFETIME = timedelta(days=30)


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


class SessionService:
    def __init__(
        self,
        wechat: WechatAuthProvider,
        repository: AuthRepository,
        field_cipher: FieldCipher,
        jwt_service: JwtService,
    ) -> None:
        self._wechat = wechat
        self._repository = repository
        self._field_cipher = field_cipher
        self._jwt = jwt_service

    async def login_with_wechat(self, code: str, device: str, now: datetime) -> SessionTokens:
        identity = await self._wechat.exchange_code(code)
        user = await self._repository.get_or_create_user(
            identity.app_id,
            self._field_cipher.encrypt(identity.openid, context=b"wechat-openid"),
            self._field_cipher.lookup_hmac(identity.openid),
            now,
        )
        if user.status in {"DELETING", "DELETED"}:
            raise AppError("ACCOUNT_DELETING", "账号注销正在处理中", 409)
        if user.status == "SUSPENDED":
            raise AppError("ACCOUNT_SUSPENDED", "账号暂不可用", 403)
        session_id = new_ulid(now)
        refresh_token = self._new_refresh_token(session_id)
        refresh_expires_at = now + REFRESH_TOKEN_LIFETIME
        await self._repository.create_session(
            SessionRecord(
                id=session_id,
                user=user,
                refresh_token_hash=_digest(refresh_token),
                expires_at=refresh_expires_at,
                device_digest=_digest(device),
            )
        )
        return self._tokens(user, session_id, refresh_token, refresh_expires_at, now)

    async def refresh(self, refresh_token: str, now: datetime) -> SessionTokens:
        session_id = self._session_id(refresh_token)
        replacement = self._new_refresh_token(session_id)
        expires_at = now + REFRESH_TOKEN_LIFETIME
        session = await self._repository.rotate_refresh(
            session_id,
            _digest(refresh_token),
            _digest(replacement),
            expires_at,
            now,
        )
        return self._tokens(session.user, session.id, replacement, expires_at, now)

    async def logout(self, session_id: str, now: datetime) -> None:
        await self._repository.revoke_session(session_id, now)

    async def revoke_all(self, user_id: str, reason: str, now: datetime) -> None:
        await self._repository.revoke_all(user_id, reason, now)

    def _tokens(
        self,
        user: UserSummary,
        session_id: str,
        refresh_token: str,
        refresh_expires_at: datetime,
        now: datetime,
    ) -> SessionTokens:
        return SessionTokens(
            session_id,
            user,
            self._jwt.issue_access_token(user.public_id, session_id, now),
            refresh_token,
            refresh_expires_at,
            {"deletion_pending": user.status == "DELETION_PENDING"},
        )

    @staticmethod
    def _new_refresh_token(session_id: str) -> str:
        return f"{session_id}.{secrets.token_urlsafe(32)}"

    @staticmethod
    def _session_id(refresh_token: str) -> str:
        session_id, separator, secret = refresh_token.partition(".")
        if not separator or len(session_id) != 26 or not secret:
            raise AppError("REFRESH_TOKEN_INVALID", "刷新凭证无效", 401)
        return session_id
