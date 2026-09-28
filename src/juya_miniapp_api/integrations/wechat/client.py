import httpx

from juya_miniapp_api.integrations.wechat.protocol import WechatIdentity
from juya_miniapp_api.shared.errors import AppError


class WechatAuthClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        app_id: str,
        app_secret: str,
    ) -> None:
        self._client = client
        self._app_id = app_id
        self._app_secret = app_secret

    async def exchange_code(self, code: str) -> WechatIdentity:
        response = await self._client.get(
            "https://api.weixin.qq.com/sns/jscode2session",
            params={
                "appid": self._app_id,
                "secret": self._app_secret,
                "js_code": code,
                "grant_type": "authorization_code",
            },
        )
        response.raise_for_status()
        payload = response.json()
        openid = payload.get("openid")
        if not isinstance(openid, str) or not openid:
            raise AppError("WECHAT_CODE_INVALID", "微信登录凭证无效", 401)
        return WechatIdentity(self._app_id, openid)
