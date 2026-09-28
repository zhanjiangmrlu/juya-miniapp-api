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
    return len(await service.execute_due_deletions(now))


async def dispatch_account_outbox(dispatcher: OutboxDispatcher, now: datetime) -> int:
    return await dispatcher.dispatch_due(now)


def create_account_cleanup_handler(
    client: AdminApiClient,
) -> Callable[[OutboxEvent], Awaitable[None]]:
    async def handle(event: OutboxEvent) -> None:
        if event.event_type != "ACCOUNT_DELETION_CLEANUP":
            raise AppError("OUTBOX_EVENT_UNSUPPORTED", "不支持的异步事件", 422)
        user_id = cast(str | None, event.payload.get("user_id"))
        request_id = cast(str | None, event.payload.get("deletion_request_id"))
        if not user_id or not request_id:
            raise AppError("OUTBOX_PAYLOAD_INVALID", "异步事件数据不完整", 422)
        await client.delete_account_data(user_id, request_id, event.id)
        raise CleanupConfirmationPending("waiting for deletion cleanup callback")

    return handle
