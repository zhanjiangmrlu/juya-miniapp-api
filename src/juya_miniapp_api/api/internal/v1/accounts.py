from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict

from juya_miniapp_api.infrastructure.security.service_hmac import ServicePrincipal
from juya_miniapp_api.modules.accounts.router import serialize_deletion
from juya_miniapp_api.modules.accounts.service import AccountLifecycleService

ServiceDependency = Callable[..., Awaitable[ServicePrincipal]]


class CleanupResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deletion_request_id: str
    succeeded: bool


# 匿名函数: clock默认时钟在调用时读取当前UTC时间
# 参数:
#     无形参。
# 返回: 带UTC时区的当前时间
def create_internal_accounts_router(
    service: AccountLifecycleService,
    *,
    current_service: ServiceDependency,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    # 功能:创建并绑定内部账号清理回执路由与业务依赖
    # 参数:
    #     service: 账号注销和学习数据清理服务,承载小程序业务操作
    #     current_service: 验证内部调用签名并返回服务身份的依赖
    #     clock: 提供当前时间的可替换时钟回调
    # 返回:包含业务端点的FastAPI路由器
    router = APIRouter(prefix="/internal/v1", tags=["internal-accounts"])

    @router.post("/users/{user_id}/deletion-cleanup-result")
    async def record_cleanup_result(
        user_id: str,
        payload: CleanupResultRequest,
        response: Response,
        _principal: Annotated[ServicePrincipal, Depends(current_service)],
    ) -> dict[str, object]:
        # 功能:接收管理端账号清理回调并更新注销最终状态
        # 参数:
        #     user_id: 当前操作所属用户的公开标识
        #     payload: 已校验的注销申请标识与管理端清理成功标记
        #     response: HTTP响应对象
        #     _principal: 已通过内部签名校验的调用服务身份
        # 返回:注销申请标识、状态和生效时间
        response.headers["Cache-Control"] = "no-store"
        result = await service.record_cross_domain_cleanup(
            user_id,
            payload.deletion_request_id,
            succeeded=payload.succeeded,
            now=clock(),
        )
        return serialize_deletion(result)

    return router
