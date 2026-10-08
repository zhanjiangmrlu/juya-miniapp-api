from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from juya_miniapp_api.infrastructure.security.service_hmac import ServicePrincipal
from juya_miniapp_api.modules.contacts.domain import AdminCorrectionView
from juya_miniapp_api.modules.contacts.service import ContactService
from juya_miniapp_api.modules.learning.admin_projection import LearningOverviewRepository
from juya_miniapp_api.modules.users.router import serialize_me
from juya_miniapp_api.modules.users.service import MeView, UserService
from juya_miniapp_api.shared.errors import AppError

ServiceDependency = Callable[..., Awaitable[ServicePrincipal]]


class UserSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    juya_number: str | None = Field(default=None, max_length=20)
    nickname: str | None = Field(default=None, max_length=64)
    wechat_id: str | None = Field(default=None, max_length=64)
    limit: int = Field(default=50, ge=1, le=100)

    @model_validator(mode="after")
    def has_search_term(self) -> "UserSearchRequest":
        # 功能:校验管理端用户搜索至少提供一个检索条件
        # 参数:
        #     self: 当前小程序的UserSearchRequest实例
        # 返回:通过搜索条件校验的当前请求模型实例
        if not any((self.juya_number, self.nickname, self.wechat_id)):
            raise ValueError("one search term is required")
        return self


class ContactStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str


class ContactProjectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_ids: list[str] = Field(min_length=1, max_length=100)


class CorrectionDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str


class CorrectionSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = Field(default=None, max_length=32)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


def _no_store(response: Response) -> None:
    # 功能:为含用户敏感信息的响应设置禁止缓存头
    # 参数:
    #     response: HTTP响应对象
    # 返回:无返回值。
    response.headers["Cache-Control"] = "no-store"


def _search_item(view: MeView) -> dict[str, object]:
    # 功能:序列化管理端用户搜索结果中的公开字段
    # 参数:
    #     view: 用户资料、联系方式和引导资格投影
    # 返回:用户公开标识、句芽编号、昵称及联系方式投影
    serialized = serialize_me(view)
    contact = serialized.pop("contact")
    contact_data = contact if isinstance(contact, dict) else {}
    serialized.update(
        {
            "wechat_id": contact_data.get("wechat_id"),
            "contact_status": contact_data.get("contact_status", "NOT_PROVIDED"),
            "contact_change_pending": contact_data.get("change_pending", False),
        }
    )
    return serialized


def _correction_body(view: AdminCorrectionView) -> dict[str, object]:
    # 功能:序列化管理端联系方式纠错详情与时间线
    # 参数:
    #     view: 管理员可见纠错详情与时间线
    # 返回:纠错申请状态、原因、操作者和联系方式时间线
    return {
        "id": view.id,
        "user_id": view.user_id,
        "juya_number": view.juya_number,
        "nickname": view.nickname,
        "wechat_id": view.wechat_id,
        "reason": view.reason,
        "status": view.status,
        "created_at": view.created_at,
        "processed_at": view.processed_at,
        "timeline": [
            {
                "status": item.status,
                "actor_type": item.actor_type,
                "actor_id": item.actor_id,
                "event_type": item.event_type,
                "occurred_at": item.occurred_at,
            }
            for item in view.timeline
        ],
    }


# 匿名函数: clock默认时钟在调用时读取当前UTC时间
# 参数:
#     无形参。
# 返回: 带UTC时区的当前时间
def create_internal_users_router(
    users: UserService,
    contacts: ContactService,
    learning_overviews: LearningOverviewRepository,
    *,
    service_dependency: ServiceDependency,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    # 功能:创建并绑定内部用户和联系方式管理路由与业务依赖
    # 参数:
    #     users: 读取和搜索用户资料的业务服务
    #     contacts: 查询和更新用户联系方式的业务服务
    #     learning_overviews: 读取管理端用户学习数量投影的仓库
    #     service_dependency: 校验内部服务身份的FastAPI依赖
    #     clock: 提供当前时间的可替换时钟回调
    # 返回:包含业务端点的FastAPI路由器
    router = APIRouter(prefix="/internal/v1", tags=["internal-users"])

    @router.post("/users/search")
    async def search_users(
        payload: UserSearchRequest,
        response: Response,
        _principal: Annotated[ServicePrincipal, Depends(service_dependency)],
        _admin_id: Annotated[str, Header(alias="X-Admin-Id")],
    ) -> dict[str, object]:
        # 功能:通过内部接口搜索用户并返回安全资料投影
        # 参数:
        #     payload: 已校验的句芽编号或昵称检索条件
        #     response: HTTP响应对象
        #     _principal: 已通过内部签名校验的调用服务身份
        #     _admin_id: 通过请求头校验的管理员标识
        # 返回:用户安全搜索结果列表
        _no_store(response)
        if payload.wechat_id:
            public_id = await contacts.find_user_by_wechat_id(payload.wechat_id)
            items = [await users.get_me(public_id)] if public_id else []
        else:
            items = await users.search(
                juya_number=payload.juya_number,
                nickname=payload.nickname,
                limit=payload.limit,
            )
        return {"items": [_search_item(item) for item in items], "has_more": False}

    @router.get("/users/search", status_code=405)
    async def reject_query_string_search() -> None:
        # 功能:拒绝通过查询串传递管理端用户检索条件
        # 参数:
        #     无形参。
        # 返回:无返回值。
        raise AppError("METHOD_NOT_ALLOWED", "用户搜索只允许 POST", 405)

    @router.post("/users/contact-projections")
    async def contact_projections(
        payload: ContactProjectionRequest,
        response: Response,
        _principal: Annotated[ServicePrincipal, Depends(service_dependency)],
        _admin_id: Annotated[str, Header(alias="X-Admin-Id")],
    ) -> dict[str, object]:
        # 功能:按请求顺序批量返回用户联系方式投影
        # 参数:
        #     payload: 已校验的批量查询的用户标识
        #     response: HTTP响应对象
        #     _principal: 已通过内部签名校验的调用服务身份
        #     _admin_id: 通过请求头校验的管理员标识
        # 返回:保持输入顺序的用户联系方式投影列表
        _no_store(response)
        projected = await contacts.get_many(tuple(payload.user_ids))
        return {
            "contacts": [
                {
                    "user_id": item.user_id,
                    "wechat_id": item.wechat_id,
                    "contact_status": item.contact_status,
                    "change_pending": item.change_pending,
                    "verified_at": item.verified_at,
                    "verified_by": item.verified_by,
                    "updated_at": item.updated_at,
                }
                for item in projected
            ]
        }

    @router.get("/users/{user_id}")
    async def user_detail(
        user_id: str,
        response: Response,
        _principal: Annotated[ServicePrincipal, Depends(service_dependency)],
        _admin_id: Annotated[str, Header(alias="X-Admin-Id")],
    ) -> dict[str, object]:
        # 功能:返回管理员可见的用户资料与联系方式详情
        # 参数:
        #     user_id: 当前操作所属用户的公开标识
        #     response: HTTP响应对象
        #     _principal: 已通过内部签名校验的调用服务身份
        #     _admin_id: 通过请求头校验的管理员标识
        # 返回:用户资料、联系方式和账号状态
        _no_store(response)
        return serialize_me(await users.get_me(user_id))

    @router.get("/users/{user_id}/learning-overview")
    async def learning_overview(
        user_id: str,
        response: Response,
        _principal: Annotated[ServicePrincipal, Depends(service_dependency)],
        _admin_id: Annotated[str, Header(alias="X-Admin-Id")],
    ) -> dict[str, int]:
        # 功能:返回管理端可见的用户学习数量统计
        # 参数:
        #     user_id: 当前操作所属用户的公开标识
        #     response: HTTP响应对象
        #     _principal: 已通过内部签名校验的调用服务身份
        #     _admin_id: 通过请求头校验的管理员标识
        # 返回:开放场景完成数量、学习日期数量和收藏数量
        _no_store(response)
        overview = await learning_overviews.get(user_id)
        return {
            "open_scene_completed_count": overview.open_scene_completed_count,
            "learning_days": overview.learning_days,
            "favorite_count": overview.favorite_count,
        }

    @router.post("/users/{user_id}/contact-status")
    async def update_contact_status(
        user_id: str,
        payload: ContactStatusRequest,
        response: Response,
        _principal: Annotated[ServicePrincipal, Depends(service_dependency)],
        admin_id: Annotated[str, Header(alias="X-Admin-Id")],
    ) -> dict[str, object]:
        # 功能:记录管理员修改的联系方式业务状态
        # 参数:
        #     user_id: 当前操作所属用户的公开标识
        #     payload: 已校验的管理员指定的联系方式状态
        #     response: HTTP响应对象
        #     _principal: 已通过内部签名校验的调用服务身份
        #     admin_id: 从X-Admin-Id请求头取得的管理员标识
        # 返回:更新后的联系方式业务状态与核验标记
        _no_store(response)
        view = await contacts.update_status(user_id, payload.status, admin_id, clock())
        return serialize_me(await users.get_me(view.user_id))

    @router.post("/users/{user_id}/contact/verify-change")
    async def verify_contact_change(
        user_id: str,
        response: Response,
        _principal: Annotated[ServicePrincipal, Depends(service_dependency)],
        admin_id: Annotated[str, Header(alias="X-Admin-Id")],
    ) -> dict[str, object]:
        # 功能:通过内部接口核验用户联系方式变更
        # 参数:
        #     user_id: 当前操作所属用户的公开标识
        #     response: HTTP响应对象
        #     _principal: 已通过内部签名校验的调用服务身份
        #     admin_id: 从X-Admin-Id请求头取得的管理员标识
        # 返回:已更新核验状态的联系方式投影
        _no_store(response)
        await contacts.verify_change(user_id, admin_id, clock())
        return serialize_me(await users.get_me(user_id))

    @router.post("/contact-corrections/search")
    async def search_contact_corrections(
        payload: CorrectionSearchRequest,
        response: Response,
        _principal: Annotated[ServicePrincipal, Depends(service_dependency)],
        _admin_id: Annotated[str, Header(alias="X-Admin-Id")],
    ) -> dict[str, object]:
        # 功能:按筛选条件分页查询管理员可见的纠错申请
        # 参数:
        #     payload: 已校验的纠错列表筛选条件与分页信息
        #     response: HTTP响应对象
        #     _principal: 已通过内部签名校验的调用服务身份
        #     _admin_id: 通过请求头校验的管理员标识
        # 返回:纠错申请列表、总数量和分页信息
        _no_store(response)
        items, total = await contacts.list_corrections(
            payload.status, payload.page, payload.page_size
        )
        return {
            "items": [_correction_body(item) for item in items],
            "total": total,
            "page": payload.page,
            "page_size": payload.page_size,
        }

    @router.get("/contact-corrections/{correction_id}")
    async def contact_correction_detail(
        correction_id: str,
        response: Response,
        _principal: Annotated[ServicePrincipal, Depends(service_dependency)],
        _admin_id: Annotated[str, Header(alias="X-Admin-Id")],
    ) -> dict[str, object]:
        # 功能:读取管理员可见的联系方式纠错申请详情
        # 参数:
        #     correction_id: 联系方式纠错申请的公开标识
        #     response: HTTP响应对象
        #     _principal: 已通过内部签名校验的调用服务身份
        #     _admin_id: 通过请求头校验的管理员标识
        # 返回:指定纠错申请及联系方式时间线详情
        _no_store(response)
        return _correction_body(await contacts.get_correction(correction_id))

    @router.post("/contact-corrections/{correction_id}/decision")
    async def decide_correction(
        correction_id: str,
        payload: CorrectionDecisionRequest,
        response: Response,
        _principal: Annotated[ServicePrincipal, Depends(service_dependency)],
        admin_id: Annotated[str, Header(alias="X-Admin-Id")],
        idempotency_key: Annotated[
            str,
            Header(alias="X-Idempotency-Key", min_length=1, max_length=128),
        ],
    ) -> dict[str, object]:
        # 功能:幂等处理管理员的联系方式纠错批准或拒绝决定
        # 参数:
        #     correction_id: 联系方式纠错申请的公开标识
        #     payload: 已校验的管理员纠错决定与说明
        #     response: HTTP响应对象
        #     _principal: 已通过内部签名校验的调用服务身份
        #     admin_id: 从X-Admin-Id请求头取得的管理员标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        # 返回:幂等决定后的纠错申请状态与详情
        _no_store(response)
        correction = await contacts.decide_correction(
            correction_id,
            payload.decision,
            admin_id,
            idempotency_key,
            clock(),
        )
        return {
            "id": correction.public_id,
            "status": correction.status,
            "processed_at": correction.processed_at,
        }

    return router
