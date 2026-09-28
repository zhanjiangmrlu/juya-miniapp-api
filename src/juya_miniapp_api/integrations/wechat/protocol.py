from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class WechatIdentity:
    app_id: str
    openid: str = field(repr=False)


class WechatAuthProvider(Protocol):
    async def exchange_code(self, code: str) -> WechatIdentity: ...
