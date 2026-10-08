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
    # 功能:计算刷新凭证或设备标识的SHA256摘要
    # 参数:
    #     value: 待计算摘要的刷新凭证或设备标识
    # 返回:刷新凭证或设备信息的SHA256摘要
    return hashlib.sha256(value.encode("utf-8")).digest()


class SessionService:
    def __init__(
        self,
        wechat: WechatAuthProvider,
        repository: AuthRepository,
        field_cipher: FieldCipher,
        jwt_service: JwtService,
    ) -> None:
        # 功能:初始化微信登录与凭证轮换服务并保存所需依赖与配置
        # 参数:
        #     self: 当前微信登录与凭证轮换服务实例
        #     wechat: 将微信临时登录码换取微信身份的客户端
        #     repository: 用户微信身份与登录会话仓库,承载登录会话业务操作
        #     field_cipher: 加解密微信身份并生成检索摘要的字段保护服务
        #     jwt_service: 签发和校验访问凭证的JWT服务
        # 返回:无返回值。
        self._wechat = wechat
        self._repository = repository
        self._field_cipher = field_cipher
        self._jwt = jwt_service

    async def login_with_wechat(self, code: str, device: str, now: datetime) -> SessionTokens:
        # 功能:使用微信身份建立登录会话并签发访问和刷新凭证
        # 参数:
        #     self: 当前微信登录与凭证轮换服务实例
        #     code: 微信客户端取得的一次性登录码
        #     device: 登录设备信息,保存前计算摘要
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:登录会话、访问凭证与刷新凭证
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
        # 功能:轮换刷新凭证并在重放时撤销会话族
        # 参数:
        #     self: 当前微信登录与凭证轮换服务实例
        #     refresh_token: 客户端持有的会话刷新凭证
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:登录会话、访问凭证与刷新凭证
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
        # 功能:撤销当前登录会话
        # 参数:
        #     self: 当前微信登录与凭证轮换服务实例
        #     session_id: 登录会话的公开标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:无返回值。
        await self._repository.revoke_session(session_id, now)

    async def revoke_all(self, user_id: str, reason: str, now: datetime) -> None:
        # 功能:撤销用户全部会话并记录撤销原因
        # 参数:
        #     self: 当前微信登录与凭证轮换服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     reason: 撤销、纠错或反馈异议的业务原因说明
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:无返回值。
        await self._repository.revoke_all(user_id, reason, now)

    def _tokens(
        self,
        user: UserSummary,
        session_id: str,
        refresh_token: str,
        refresh_expires_at: datetime,
        now: datetime,
    ) -> SessionTokens:
        # 功能:组装访问凭证、刷新凭证与账号注销等待状态
        # 参数:
        #     self: 当前微信登录与凭证轮换服务实例
        #     user: 已识别用户的公开标识、句芽编号与账号状态
        #     session_id: 登录会话的公开标识
        #     refresh_token: 客户端持有的会话刷新凭证
        #     refresh_expires_at: 刷新凭证与登录会话的失效时间
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:登录会话、访问凭证与刷新凭证
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
        # 功能:生成包含会话标识与随机密钥的刷新凭证
        # 参数:
        #     session_id: 登录会话的公开标识
        # 返回:包含会话标识与随机密钥的刷新凭证
        return f"{session_id}.{secrets.token_urlsafe(32)}"

    @staticmethod
    def _session_id(refresh_token: str) -> str:
        # 功能:从刷新凭证中提取并校验会话标识
        # 参数:
        #     refresh_token: 客户端持有的会话刷新凭证
        # 返回:从刷新凭证提取的会话公开标识
        session_id, separator, secret = refresh_token.partition(".")
        if not separator or len(session_id) != 26 or not secret:
            raise AppError("REFRESH_TOKEN_INVALID", "刷新凭证无效", 401)
        return session_id
