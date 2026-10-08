from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from juya_miniapp_api.modules.accounts.domain import DeletionRequest
from juya_miniapp_api.modules.accounts.repository import AccountRepository
from juya_miniapp_api.shared.errors import AppError

SessionRevoker = Callable[[str, str, datetime], Awaitable[None]]


class AccountLifecycleService:
    def __init__(self, repository: AccountRepository, revoke_sessions: SessionRevoker) -> None:
        # 功能:初始化账号注销和学习数据清理服务并保存所需依赖与配置
        # 参数:
        #     self: 当前账号注销和学习数据清理服务实例
        #     repository: 账号注销与清理仓库,承载账号注销业务操作
        #     revoke_sessions: 按用户撤销全部登录会话的异步回调
        # 返回:无返回值。
        self._repository = repository
        self._revoke_sessions = revoke_sessions

    async def clear_learning_data(self, user_id: str, confirmation: str) -> None:
        # 功能:清空用户学习记录、收藏、复习和打卡数据
        # 参数:
        #     self: 当前账号注销和学习数据清理服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     confirmation: 用户显式确认清空学习数据的确认字符串
        # 返回:无返回值。
        if confirmation != "CLEAR_LEARNING_DATA":
            raise AppError("CONFIRMATION_REQUIRED", "请确认清空学习数据", 422)
        await self._repository.clear_learning_data(user_id)

    async def request_deletion(self, user_id: str, now: datetime) -> DeletionRequest:
        # 功能:幂等创建账号注销申请并设置七天等待期
        # 参数:
        #     self: 当前账号注销和学习数据清理服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:账号注销申请及执行状态
        return await self._repository.request_deletion(user_id, now, now + timedelta(days=7))

    async def revoke_deletion(self, user_id: str, now: datetime) -> DeletionRequest:
        # 功能:撤回等待期内的账号注销申请
        # 参数:
        #     self: 当前账号注销和学习数据清理服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:账号注销申请及执行状态
        return await self._repository.revoke_deletion(user_id, now)

    async def execute_due_deletions(self, now: datetime) -> list[DeletionRequest]:
        # 功能:启动到期注销并撤销账号的全部登录会话
        # 参数:
        #     self: 当前账号注销和学习数据清理服务实例
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:账号注销申请及执行状态集合
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
        # 功能:记录跨域清理回执并在全部成功后完成账号注销
        # 参数:
        #     self: 当前账号注销和学习数据清理服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     request_id: 账号注销申请的公开标识,用于匹配跨域清理回执
        #     succeeded: 管理端跨域清理是否已确认成功
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:账号注销申请及执行状态
        return await self._repository.record_cross_domain_cleanup(
            user_id, request_id, succeeded=succeeded, now=now
        )
