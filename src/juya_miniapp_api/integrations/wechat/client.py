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
        # 功能:初始化微信身份交换客户端并保存所需依赖与配置
        # 参数:
        #     self: 当前微信身份交换客户端实例
        #     client: 异步HTTP客户端
        #     app_id: 微信小程序应用标识
        #     app_secret: 换取微信登录身份的应用密钥
        # 返回:无返回值。
        self._client = client
        self._app_id = app_id
        self._app_secret = app_secret

    async def exchange_code(self, code: str) -> WechatIdentity:
        # 功能:使用微信临时登录码换取应用身份与openid
        # 参数:
        #     self: 当前微信身份交换客户端实例
        #     code: 微信客户端取得的一次性登录码
        # 返回:微信应用标识与openid身份
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
