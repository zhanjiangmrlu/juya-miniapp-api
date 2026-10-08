from datetime import datetime, timedelta
from typing import Any

import jwt

from juya_miniapp_api.shared.errors import AppError

ACCESS_TOKEN_LIFETIME = timedelta(hours=2)


class JwtService:
    def __init__(self, secret: str | bytes, *, kid: str) -> None:
        # 功能:初始化访问凭证签发与验证服务并保存所需依赖与配置
        # 参数:
        #     self: 当前访问凭证签发与验证服务实例
        #     secret: JWT访问凭证签名和验签的密钥
        #     kid: JWT签名密钥版本标识
        # 返回:无返回值。
        self._secret = secret
        self._kid = kid

    def issue_access_token(self, user_id: str, session_id: str, now: datetime) -> str:
        # 功能:签发包含用户与会话标识的短期访问凭证
        # 参数:
        #     self: 当前访问凭证签发与验证服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     session_id: 登录会话的公开标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:已签名的短期JWT访问凭证
        issued_at = int(now.timestamp())
        payload = {
            "aud": "miniapp",
            "sub": user_id,
            "sid": session_id,
            "iat": issued_at,
            "exp": issued_at + int(ACCESS_TOKEN_LIFETIME.total_seconds()),
            "kid": self._kid,
        }
        return jwt.encode(
            payload,
            self._secret,
            algorithm="HS256",
            headers={"kid": self._kid},
        )

    def decode_access_token(self, token: str, *, now: datetime) -> dict[str, Any]:
        # 功能:校验访问凭证签名、声明和有效期
        # 参数:
        #     self: 当前访问凭证签发与验证服务实例
        #     token: 待校验的JWT访问凭证
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:通过签名与时效校验的JWT声明字典
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                audience="miniapp",
                options={"verify_exp": False, "verify_iat": False},
            )
        except jwt.PyJWTError as error:
            raise AppError("ACCESS_TOKEN_INVALID", "登录凭证无效", 401) from error
        if int(now.timestamp()) >= int(claims["exp"]):
            raise AppError("ACCESS_TOKEN_EXPIRED", "登录凭证已过期", 401)
        return claims
