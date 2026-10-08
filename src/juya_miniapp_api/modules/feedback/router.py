from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field

from juya_miniapp_api.infrastructure.redis.rate_limit import RateLimiter, enforce_rate_limit
from juya_miniapp_api.integrations.oss.upload import OssUploadService
from juya_miniapp_api.modules.feedback.service import FeedbackService
from juya_miniapp_api.modules.users.router import UserDependency


class FeedbackCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=300)
    source: dict[str, Any] = Field(default_factory=dict)
    screenshots: list[str] = Field(default_factory=list, max_length=1)


class SupplementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=300)
    screenshots: list[str] = Field(default_factory=list, max_length=1)


class ResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    reason: str | None = Field(default=None, max_length=300)


class FeedbackUploadCredentialResponse(BaseModel):
    host: str
    key: str
    fields: dict[str, str]
    content_type: str
    max_bytes: int
    expires_at: datetime
    # Existing response fields remain additive-compatible; clients submit V4 fields.
    policy: str
    signature: str
    access_key_id: str


# 匿名函数: clock默认时钟在调用时读取当前UTC时间
# 参数:
#     无形参。
# 返回: 带UTC时区的当前时间
def create_feedback_router(
    service: FeedbackService,
    uploads: OssUploadService,
    *,
    user_dependency: UserDependency,
    rate_limiter: RateLimiter | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    # 功能:创建并绑定用户反馈路由与业务依赖
    # 参数:
    #     service: 用户反馈校验与上游编排服务,承载用户反馈业务操作
    #     uploads: 为用户头像或反馈截图签发OSS上传策略的服务
    #     user_dependency: 校验登录凭证并取得当前用户标识的FastAPI依赖
    #     rate_limiter: 按主体和业务范围控制请求频率的限流服务
    #     clock: 提供当前时间的可替换时钟回调
    # 返回:包含业务端点的FastAPI路由器
    router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])

    @router.get("")
    async def list_feedback(
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        # 功能:列出当前用户提交的反馈
        # 参数:
        #     user_id: 当前操作所属用户的公开标识
        # 返回:当前用户反馈列表与分页标记
        return {"items": await service.list_feedback(user_id), "has_more": False}

    @router.post("")
    async def create_feedback(
        payload: FeedbackCreateRequest,
        user_id: Annotated[str, Depends(user_dependency)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, Any]:
        # 功能:提交当前用户反馈及来源和截图对象键
        # 参数:
        #     payload: 已校验的反馈分类、说明、来源与截图对象键
        #     user_id: 当前操作所属用户的公开标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        # 返回:新建反馈记录及其处理状态
        await enforce_rate_limit(
            rate_limiter,
            "feedback",
            user_id,
            limit=5,
            window=timedelta(hours=1),
            now=clock(),
        )
        return await service.create(
            user_id,
            payload.category,
            payload.description,
            payload.source,
            payload.screenshots,
            idempotency_key,
        )

    @router.post("/uploads", response_model=FeedbackUploadCredentialResponse)
    async def feedback_upload(
        user_id: Annotated[str, Depends(user_dependency)],
        content_type: Annotated[str, Query(alias="content_type")],
    ) -> dict[str, object]:
        # 功能:签发当前用户反馈截图的直传凭证
        # 参数:
        #     user_id: 当前操作所属用户的公开标识
        #     content_type: 待上传图片的MIME类型,仅接受受支持图片格式
        # 返回:绑定当前用户的截图直传地址、表单字段和到期时间
        await enforce_rate_limit(
            rate_limiter,
            "feedback_upload",
            user_id,
            limit=10,
            window=timedelta(hours=1),
            now=clock(),
        )
        return uploads.create_feedback_upload(user_id, content_type)

    @router.get("/{feedback_id}")
    async def feedback_detail(
        feedback_id: str,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, Any]:
        # 功能:读取并校验当前用户反馈详情
        # 参数:
        #     feedback_id: 用户反馈记录的公开标识
        #     user_id: 当前操作所属用户的公开标识
        # 返回:经归属校验的反馈说明、截图与处理进度
        return await service.detail(user_id, feedback_id)

    @router.post("/{feedback_id}/supplements")
    async def supplement(
        feedback_id: str,
        payload: SupplementRequest,
        user_id: Annotated[str, Depends(user_dependency)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, Any]:
        # 功能:校验反馈归属与截图数量并补充反馈内容
        # 参数:
        #     feedback_id: 用户反馈记录的公开标识
        #     payload: 已校验的反馈补充文本与截图对象键
        #     user_id: 当前操作所属用户的公开标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        # 返回:补充说明与截图后更新的反馈记录
        return await service.supplement(
            user_id, feedback_id, payload.text, idempotency_key, screenshots=payload.screenshots
        )

    @router.post("/{feedback_id}/resolution")
    async def resolution(
        feedback_id: str,
        payload: ResolutionRequest,
        user_id: Annotated[str, Depends(user_dependency)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, Any]:
        # 功能:提交当前用户对反馈处理结果的确认或异议
        # 参数:
        #     feedback_id: 用户反馈记录的公开标识
        #     payload: 已校验的反馈处理结果确认动作与原因
        #     user_id: 当前操作所属用户的公开标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        # 返回:确认处理结果或提出异议后更新的反馈记录
        return await service.resolve(
            user_id,
            feedback_id,
            payload.action,
            payload.reason,
            idempotency_key,
        )

    return router
