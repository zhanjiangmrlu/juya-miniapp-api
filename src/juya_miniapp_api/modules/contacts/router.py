from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response

from juya_miniapp_api.infrastructure.redis.rate_limit import RateLimiter, enforce_rate_limit
from juya_miniapp_api.modules.contacts.service import ContactService
from juya_miniapp_api.modules.users.router import UserDependency, _contact
from juya_miniapp_api.modules.users.schemas import ContactSaveRequest, CorrectionCreateRequest
from juya_miniapp_api.shared.errors import AppError


# 匿名函数: clock默认时钟在调用时读取当前UTC时间
# 参数:
#     无形参。
# 返回: 带UTC时区的当前时间
def create_contacts_router(
    service: ContactService,
    *,
    user_dependency: UserDependency,
    rate_limiter: RateLimiter | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    # 功能:创建并绑定联系方式路由与业务依赖
    # 参数:
    #     service: 联系方式保存、撤回和纠错服务,承载联系方式业务操作
    #     user_dependency: 校验登录凭证并取得当前用户标识的FastAPI依赖
    #     rate_limiter: 按主体和业务范围控制请求频率的限流服务
    #     clock: 提供当前时间的可替换时钟回调
    # 返回:包含业务端点的FastAPI路由器
    router = APIRouter(prefix="/api/v1/me/contact", tags=["contact"])

    @router.post("/prompt-exposures")
    async def prompt_exposure(
        user_id: Annotated[str, Depends(user_dependency)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, bool]:
        # 功能:幂等记录当前用户联系方式引导的首次曝光
        # 参数:
        #     user_id: 当前操作所属用户的公开标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        # 返回:created字段,标记是否首次创建联系方式引导曝光
        return {"created": await service.record_prompt_exposure(user_id, idempotency_key, clock())}

    @router.get("")
    async def get_contact(
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object] | None:
        # 功能:读取当前用户联系方式状态
        # 参数:
        #     response: HTTP响应对象
        #     user_id: 当前操作所属用户的公开标识
        # 返回:联系方式业务状态、修改资格和核验标记;原记录不存在时为None
        response.headers["Cache-Control"] = "private, no-store"
        return _contact(await service.get(user_id))

    @router.put("")
    async def save_contact(
        payload: ContactSaveRequest,
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object] | None:
        # 功能:保存联系方式并限制真实变更次数与纠错状态
        # 参数:
        #     payload: 已校验的微信联系方式与同意条款版本
        #     response: HTTP响应对象
        #     user_id: 当前操作所属用户的公开标识
        # 返回:保存后的联系方式状态、修改资格与纠错信息;原记录不存在时为None
        if not payload.consent_confirmed:
            raise AppError("CONTACT_CONSENT_REQUIRED", "请确认联系方式用途", 422)
        await enforce_rate_limit(
            rate_limiter,
            "contact_update",
            user_id,
            limit=5,
            window=timedelta(hours=1),
            now=clock(),
        )
        response.headers["Cache-Control"] = "private, no-store"
        return _contact(
            await service.save(
                user_id,
                payload.wechat_id,
                payload.consent_version,
                payload.source,
                clock(),
            )
        )

    @router.delete("", status_code=204)
    async def withdraw_contact(
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> None:
        # 功能:撤回当前用户联系方式并返回撤回状态
        # 参数:
        #     user_id: 当前操作所属用户的公开标识
        # 返回:无返回值。
        await service.withdraw(user_id, clock())

    @router.post("/corrections", status_code=201)
    async def create_correction(
        payload: CorrectionCreateRequest,
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        # 功能:创建联系方式纠错申请并限制重复有效申请
        # 参数:
        #     payload: 已校验的联系方式纠错原因
        #     response: HTTP响应对象
        #     user_id: 当前操作所属用户的公开标识
        # 返回:纠错申请公开标识与待处理状态
        correction = await service.request_correction(user_id, payload.reason, clock())
        response.headers["Cache-Control"] = "private, no-store"
        return {
            "id": correction.public_id,
            "status": correction.status,
            "created_at": correction.created_at,
        }

    return router
