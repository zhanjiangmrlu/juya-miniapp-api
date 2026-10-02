"""Socket acceptance entrypoint: replace only the external WeChat provider."""

from juya_miniapp_api.integrations.wechat.client import WechatAuthClient
from juya_miniapp_api.integrations.wechat.protocol import WechatIdentity


async def fixture_exchange(self: WechatAuthClient, code: str) -> WechatIdentity:
    assert code.startswith("acceptance-")
    return WechatIdentity("wx-acceptance", code)


WechatAuthClient.exchange_code = fixture_exchange

from juya_miniapp_api.main import app  # noqa: E402, F401
