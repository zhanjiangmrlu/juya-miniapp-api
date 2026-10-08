from datetime import datetime

from juya_miniapp_api.infrastructure.observability.metrics import MESSAGE_BACKLOG
from juya_miniapp_api.modules.messages.domain import InboxMessage, MessagePage
from juya_miniapp_api.modules.messages.repository import MessageRepository


class MessageService:
    def __init__(self, repository: MessageRepository) -> None:
        # 功能:初始化用户站内消息读写服务并保存所需依赖与配置
        # 参数:
        #     self: 当前用户站内消息读写服务实例
        #     repository: 用户站内消息仓库,承载站内消息业务操作
        # 返回:无返回值。
        self._repository = repository

    async def create(
        self,
        user_id: str,
        event_id: str,
        message_type: str,
        title: str,
        summary: str,
        related_type: str | None,
        related_id: str | None,
        now: datetime,
    ) -> InboxMessage:
        # 功能:幂等创建用户站内消息
        # 参数:
        #     self: 当前用户站内消息读写服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     event_id: 业务事件或发件箱事件的去重标识
        #     message_type: 站内消息的业务类别
        #     title: 站内消息向用户展示的标题
        #     summary: 站内消息向用户展示的摘要文本
        #     related_type: 站内消息关联业务对象的类别
        #     related_id: 站内消息关联业务记录的公开标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:站内消息、关联对象与已读时间
        return await self._repository.create_once(
            user_id,
            event_id,
            message_type,
            title,
            summary,
            related_type,
            related_id,
            now,
        )

    async def list_messages(
        self, user_id: str, *, cursor: str | None = None, limit: int = 50
    ) -> MessagePage:
        # 功能:按游标分页查询用户站内消息
        # 参数:
        #     self: 当前用户站内消息读写服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     cursor: 分页游标;空值从第一页开始
        #     limit: 本次查询或任务领取允许的最大记录数量
        # 返回:站内消息列表、下一页游标和未读数量
        values = await self._repository.list_messages(user_id, after_id=cursor, limit=limit + 1)
        visible = values[:limit]
        return MessagePage(
            visible,
            visible[-1].id if len(values) > limit else None,
            len(values) > limit,
        )

    async def mark_read(self, user_id: str, message_id: str, now: datetime) -> InboxMessage:
        # 功能:校验消息归属并幂等记录已读时间
        # 参数:
        #     self: 当前用户站内消息读写服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     message_id: 用户站内消息的公开标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:站内消息、关联对象与已读时间
        return await self._repository.mark_read(user_id, message_id, now)

    async def unread_count(self, user_id: str) -> int:
        # 功能:统计用户未读站内消息数量
        # 参数:
        #     self: 当前用户站内消息读写服务实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:当前用户未读消息数量
        count = await self._repository.count_unread(user_id)
        MESSAGE_BACKLOG.set(count)
        return count
