from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from juya_miniapp_api.infrastructure.security.service_hmac import ServicePrincipal
from juya_miniapp_api.modules.messages.router import serialize_message
from juya_miniapp_api.modules.messages.service import MessageService


class InternalMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=128)
    message_type: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=500)
    related_type: str | None = Field(default=None, max_length=64)
    related_id: str | None = Field(default=None, max_length=64)


ServiceDependency = Callable[..., Awaitable[ServicePrincipal]]


# 匿名函数: clock默认时钟在调用时读取当前UTC时间
# 参数:
#     无形参。
# 返回: 带UTC时区的当前时间
def create_internal_messages_router(
    service: MessageService,
    *,
    current_service: ServiceDependency,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    # 功能:创建并绑定内部站内消息路由与业务依赖
    # 参数:
    #     service: 用户站内消息读写服务,承载小程序业务操作
    #     current_service: 验证内部调用签名并返回服务身份的依赖
    #     clock: 提供当前时间的可替换时钟回调
    # 返回:包含业务端点的FastAPI路由器
    router = APIRouter(prefix="/internal/v1", tags=["internal-messages"])

    @router.post("/users/{user_id}/messages", status_code=201)
    async def create_message(
        user_id: str,
        payload: InternalMessageRequest,
        response: Response,
        _principal: Annotated[ServicePrincipal, Depends(current_service)],
    ) -> dict[str, object]:
        # 功能:通过内部接口幂等创建用户站内消息
        # 参数:
        #     user_id: 当前操作所属用户的公开标识
        #     payload: 已校验的业务事件标识、消息内容与关联对象
        #     response: HTTP响应对象
        #     _principal: 已通过内部签名校验的调用服务身份
        # 返回:站内消息标识、类型、标题、摘要、关联对象与已读时间
        response.headers["Cache-Control"] = "no-store"
        item = await service.create(
            user_id,
            payload.event_id,
            payload.message_type,
            payload.title,
            payload.summary,
            payload.related_type,
            payload.related_id,
            clock(),
        )
        return serialize_message(item)

    return router
