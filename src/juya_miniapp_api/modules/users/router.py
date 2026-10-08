from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from juya_miniapp_api.integrations.oss.upload import OssUploadService
from juya_miniapp_api.modules.contacts.domain import ContactView
from juya_miniapp_api.modules.users.schemas import (
    AvatarUploadCredentialResponse,
    AvatarUploadRequest,
    ProfileUpdate,
)
from juya_miniapp_api.modules.users.service import MeView, UserService

UserDependency = Callable[[], Awaitable[str]]


def _contact(contact: ContactView | None) -> dict[str, object] | None:
    # 功能:序列化用户可见的联系方式状态
    # 参数:
    #     contact: 待序列化的用户联系方式可见投影
    # 返回:用户联系方式状态、修改机会与核验信息;原记录不存在时为None
    if contact is None:
        return None
    return {
        "wechat_id": contact.wechat_id,
        "self_edit_count": contact.self_edit_count,
        "change_pending": contact.change_pending,
        "contact_status": contact.contact_status,
        "can_self_edit": contact.can_self_edit,
        "requires_correction": contact.requires_correction,
        "consent_version": contact.consent_version,
        "consented_at": contact.consented_at,
        "withdrawn_at": contact.withdrawn_at,
        "verified_at": contact.verified_at,
        "verified_by": contact.verified_by,
        "updated_at": contact.updated_at,
    }


def serialize_me(view: MeView) -> dict[str, object]:
    # 功能:序列化当前用户资料、联系方式与引导状态
    # 参数:
    #     view: 用户资料、联系方式和引导资格投影
    # 返回:用户公开资料、头像、联系方式与引导资格
    return {
        "public_id": view.profile.public_id,
        "juya_number": view.profile.juya_number,
        "status": view.profile.status,
        "nickname": view.profile.nickname,
        "avatar_object_key": view.profile.avatar_object_key,
        "created_at": view.profile.created_at,
        "last_active_at": view.profile.last_active_at,
        "contact": _contact(view.contact),
        "contact_prompt_eligible": view.contact_prompt_eligible,
        "deletion": view.deletion,
    }


# 匿名函数: clock默认时钟在调用时读取当前UTC时间
# 参数:
#     无形参。
# 返回: 带UTC时区的当前时间
def create_users_router(
    service: UserService,
    *,
    user_dependency: UserDependency,
    uploads: OssUploadService | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    # 功能:创建并绑定用户资料路由与业务依赖
    # 参数:
    #     service: 用户资料查询更新服务,承载用户资料业务操作
    #     user_dependency: 校验登录凭证并取得当前用户标识的FastAPI依赖
    #     uploads: 为用户头像或反馈截图签发OSS上传策略的服务
    #     clock: 提供当前时间的可替换时钟回调
    # 返回:包含业务端点的FastAPI路由器
    router = APIRouter(prefix="/api/v1", tags=["me"])

    @router.post("/me/avatar/upload-policy", response_model=AvatarUploadCredentialResponse)
    async def avatar_upload_policy(
        payload: AvatarUploadRequest,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        # 功能:为当前用户签发头像图片直传策略
        # 参数:
        #     payload: 已校验的头像图片MIME类型
        #     user_id: 当前操作所属用户的公开标识
        # 返回:当前用户头像对象键、V4上传表单字段与有效期
        from juya_miniapp_api.shared.errors import AppError

        if uploads is None:
            raise AppError("AVATAR_STORAGE_UNAVAILABLE", "头像上传暂不可用", 503)
        return uploads.create_avatar_upload(user_id, payload.content_type)

    @router.get("/me")
    async def get_me(
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        # 功能:读取账号资料、联系方式与引导资格
        # 参数:
        #     response: HTTP响应对象
        #     user_id: 当前操作所属用户的公开标识
        # 返回:用户公开资料、联系方式、注销等待与联系方式引导资格
        response.headers["Cache-Control"] = "private, no-store"
        return serialize_me(await service.get_me(user_id))

    @router.patch("/me/profile")
    async def update_profile(
        payload: ProfileUpdate,
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        # 功能:校验头像对象归属并更新用户昵称与头像资料
        # 参数:
        #     payload: 已校验的待更新的昵称和头像对象键
        #     response: HTTP响应对象
        #     user_id: 当前操作所属用户的公开标识
        # 返回:更新后的用户昵称、头像与联系方式投影
        response.headers["Cache-Control"] = "private, no-store"
        return serialize_me(
            await service.update_profile(
                user_id, payload.nickname, payload.avatar_object_key, clock()
            )
        )

    return router
