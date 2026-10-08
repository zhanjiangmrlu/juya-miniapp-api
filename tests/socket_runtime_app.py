"""Socket acceptance entrypoint: replace only the external WeChat provider."""

from juya_miniapp_api.integrations.wechat.client import WechatAuthClient
from juya_miniapp_api.integrations.wechat.protocol import WechatIdentity


async def fixture_exchange(self: WechatAuthClient, code: str) -> WechatIdentity:
    # 功能:在测试中在Socket验收中将登录码转换为固定微信测试身份
    # 参数:
    #     self: 当前微信身份交换客户端实例
    #     code: 微信客户端取得的一次性登录码
    # 返回:微信应用标识与openid身份
    assert code.startswith("acceptance-")
    return WechatIdentity("wx-acceptance", code)


WechatAuthClient.exchange_code = fixture_exchange

from juya_miniapp_api.main import app  # noqa: E402, F401
