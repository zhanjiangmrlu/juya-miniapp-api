from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class WechatIdentity:
    app_id: str
    openid: str = field(repr=False)


class WechatAuthProvider(Protocol):
    async def exchange_code(self, code: str) -> WechatIdentity:
        # 功能:使用微信临时登录码换取应用身份与openid
        # 参数:
        #     self: 当前微信身份交换提供器实例
        #     code: 微信客户端取得的一次性登录码
        # 返回:微信应用标识与openid身份
        ...
