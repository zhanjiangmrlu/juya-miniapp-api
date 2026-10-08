from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import cast

from juya_miniapp_api.infrastructure.tasks.outbox import OutboxDispatcher
from juya_miniapp_api.integrations.admin_api.client import AdminApiClient
from juya_miniapp_api.modules.accounts.domain import OutboxEvent
from juya_miniapp_api.modules.accounts.service import AccountLifecycleService
from juya_miniapp_api.shared.errors import AppError


class CleanupConfirmationPending(RuntimeError):
    """The cleanup command was accepted but the signed callback has not arrived."""


async def execute_due_account_deletions(service: AccountLifecycleService, now: datetime) -> int:
    # 功能:启动到期账号注销任务并返回启动数量
    # 参数:
    #     service: 账号注销和学习数据清理服务,承载账号注销业务操作
    #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
    # 返回:本次启动的到期注销申请数量
    return len(await service.execute_due_deletions(now))


async def dispatch_account_outbox(dispatcher: OutboxDispatcher, now: datetime) -> int:
    # 功能:启动账号注销发件箱事件投递任务
    # 参数:
    #     dispatcher: 领取并投递发件箱事件的调度器
    #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
    # 返回:本次尝试投递的账号清理事件数量
    return await dispatcher.dispatch_due(now)


def create_account_cleanup_handler(
    client: AdminApiClient,
) -> Callable[[OutboxEvent], Awaitable[None]]:
    # 功能:创建调用管理端清理账号数据的发件箱回调
    # 参数:
    #     client: 带内部HMAC鉴权的管理端客户端
    # 返回:接收一条注销清理事件的异步回调
    async def handle(event: OutboxEvent) -> None:
        # 功能:校验注销清理事件并通知管理端执行跨域清理
        # 参数:
        #     event: 需要投递或断言的跨域清理发件箱事件
        # 返回:无返回值。
        if event.event_type != "ACCOUNT_DELETION_CLEANUP":
            raise AppError("OUTBOX_EVENT_UNSUPPORTED", "不支持的异步事件", 422)
        user_id = cast(str | None, event.payload.get("user_id"))
        request_id = cast(str | None, event.payload.get("deletion_request_id"))
        if not user_id or not request_id:
            raise AppError("OUTBOX_PAYLOAD_INVALID", "异步事件数据不完整", 422)
        await client.delete_account_data(user_id, request_id, event.id)
        raise CleanupConfirmationPending("waiting for deletion cleanup callback")

    return handle
