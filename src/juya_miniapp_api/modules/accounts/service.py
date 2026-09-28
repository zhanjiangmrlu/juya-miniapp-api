from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from juya_miniapp_api.modules.accounts.domain import DeletionRequest
from juya_miniapp_api.modules.accounts.repository import AccountRepository
from juya_miniapp_api.shared.errors import AppError

SessionRevoker = Callable[[str, str, datetime], Awaitable[None]]


class AccountLifecycleService:
    def __init__(self, repository: AccountRepository, revoke_sessions: SessionRevoker) -> None:
        self._repository = repository
        self._revoke_sessions = revoke_sessions

    async def clear_learning_data(self, user_id: str, confirmation: str) -> None:
        if confirmation != "CLEAR_LEARNING_DATA":
            raise AppError("CONFIRMATION_REQUIRED", "请确认清空学习数据", 422)
        await self._repository.clear_learning_data(user_id)

    async def request_deletion(self, user_id: str, now: datetime) -> DeletionRequest:
        return await self._repository.request_deletion(user_id, now, now + timedelta(days=7))

    async def revoke_deletion(self, user_id: str, now: datetime) -> DeletionRequest:
        return await self._repository.revoke_deletion(user_id, now)

    async def execute_due_deletions(self, now: datetime) -> list[DeletionRequest]:
        started = await self._repository.begin_due_deletions(now)
        for request in started:
            await self._revoke_sessions(request.user_id, "ACCOUNT_DELETION", now)
        return started

    async def record_cross_domain_cleanup(
        self,
        user_id: str,
        request_id: str,
        *,
        succeeded: bool,
        now: datetime,
    ) -> DeletionRequest:
        return await self._repository.record_cross_domain_cleanup(
            user_id, request_id, succeeded=succeeded, now=now
        )
