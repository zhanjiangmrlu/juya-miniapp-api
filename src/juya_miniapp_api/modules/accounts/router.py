from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict

from juya_miniapp_api.modules.accounts.domain import DeletionRequest
from juya_miniapp_api.modules.accounts.service import AccountLifecycleService

UserDependency = Callable[[], Awaitable[str]]


class ClearLearningDataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: str


def serialize_deletion(request: DeletionRequest) -> dict[str, object]:
    # 功能:序列化账号注销申请状态与生效时间
    # 参数:
    #     request: 账号注销申请及执行状态
    # 返回:注销申请的公开标识、状态、申请时间和生效时间
    return {
        "id": request.id,
        "status": request.status,
        "requested_at": request.requested_at,
        "effective_at": request.effective_at,
        "revoked_at": request.revoked_at,
        "completed_at": request.completed_at,
    }


# 匿名函数: clock默认时钟在调用时读取当前UTC时间
# 参数:
#     无形参。
# 返回: 带UTC时区的当前时间
def create_accounts_router(
    service: AccountLifecycleService,
    *,
    user_dependency: UserDependency,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    # 功能:创建并绑定账号生命周期路由与业务依赖
    # 参数:
    #     service: 账号注销和学习数据清理服务,承载账号注销业务操作
    #     user_dependency: 校验登录凭证并取得当前用户标识的FastAPI依赖
    #     clock: 提供当前时间的可替换时钟回调
    # 返回:包含业务端点的FastAPI路由器
    router = APIRouter(prefix="/api/v1/me", tags=["account-privacy"])

    @router.delete("/learning-data", status_code=204)
    async def clear_learning_data(
        payload: ClearLearningDataRequest,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> None:
        # 功能:清空用户学习记录、收藏、复习和打卡数据
        # 参数:
        #     payload: 已校验的清空学习数据确认字符串
        #     user_id: 当前操作所属用户的公开标识
        # 返回:无返回值。
        await service.clear_learning_data(user_id, payload.confirmation)

    @router.post("/deletion", status_code=202)
    async def request_deletion(
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        # 功能:幂等创建账号注销申请并设置七天等待期
        # 参数:
        #     response: HTTP响应对象
        #     user_id: 当前操作所属用户的公开标识
        # 返回:注销申请标识、等待状态与生效时间
        response.headers["Cache-Control"] = "no-store"
        return serialize_deletion(await service.request_deletion(user_id, clock()))

    @router.post("/deletion/revoke")
    async def revoke_deletion(
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        # 功能:撤回等待期内的账号注销申请
        # 参数:
        #     response: HTTP响应对象
        #     user_id: 当前操作所属用户的公开标识
        # 返回:已撤回的注销申请状态与原生效时间
        response.headers["Cache-Control"] = "no-store"
        return serialize_deletion(await service.revoke_deletion(user_id, clock()))

    return router
