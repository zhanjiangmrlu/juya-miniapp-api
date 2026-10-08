from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from juya_miniapp_api.modules.messages.domain import InboxMessage
from juya_miniapp_api.modules.messages.service import MessageService
from juya_miniapp_api.modules.users.router import UserDependency


def serialize_message(item: InboxMessage) -> dict[str, object]:
    # 功能:序列化站内消息及关联业务信息
    # 参数:
    #     item: 站内消息、关联对象与已读时间
    # 返回:消息标识、类型、标题摘要、关联对象和已读时间
    return {
        "id": item.id,
        "type": item.message_type,
        "title": item.title,
        "summary": item.summary,
        "related_type": item.related_type,
        "related_id": item.related_id,
        "created_at": item.created_at,
        "read_at": item.read_at,
    }


# 匿名函数: clock默认时钟在调用时读取当前UTC时间
# 参数:
#     无形参。
# 返回: 带UTC时区的当前时间
def create_messages_router(
    service: MessageService,
    *,
    user_dependency: UserDependency,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    # 功能:创建并绑定站内消息路由与业务依赖
    # 参数:
    #     service: 用户站内消息读写服务,承载站内消息业务操作
    #     user_dependency: 校验登录凭证并取得当前用户标识的FastAPI依赖
    #     clock: 提供当前时间的可替换时钟回调
    # 返回:包含业务端点的FastAPI路由器
    router = APIRouter(prefix="/api/v1/messages", tags=["messages"])

    @router.get("")
    async def messages(
        user_id: Annotated[str, Depends(user_dependency)],
        cursor: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> dict[str, object]:
        # 功能:分页返回当前用户站内消息及未读数量
        # 参数:
        #     user_id: 当前操作所属用户的公开标识
        #     cursor: 分页游标;空值从第一页开始
        #     limit: 本次查询或任务领取允许的最大记录数量
        # 返回:消息列表、下一页游标、是否还有后续页与未读数量
        page = await service.list_messages(user_id, cursor=cursor, limit=limit)
        return {
            "items": [serialize_message(item) for item in page.items],
            "next_cursor": page.next_cursor,
            "has_more": page.has_more,
        }

    @router.post("/{message_id}/read")
    async def read_message(
        message_id: str,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        # 功能:标记当前用户站内消息已读并返回消息字段
        # 参数:
        #     message_id: 用户站内消息的公开标识
        #     user_id: 当前操作所属用户的公开标识
        # 返回:当前消息的内容、关联对象与已读时间
        return serialize_message(await service.mark_read(user_id, message_id, clock()))

    return router
