from datetime import datetime, timedelta
from typing import Any

import jwt

from juya_miniapp_api.shared.errors import AppError

ACCESS_TOKEN_LIFETIME = timedelta(hours=2)


class JwtService:
    def __init__(self, secret: str | bytes, *, kid: str) -> None:
        self._secret = secret
        self._kid = kid

    def issue_access_token(self, user_id: str, session_id: str, now: datetime) -> str:
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
