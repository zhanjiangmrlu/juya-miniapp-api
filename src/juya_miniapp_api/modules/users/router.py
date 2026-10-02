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


def create_users_router(
    service: UserService,
    *,
    user_dependency: UserDependency,
    uploads: OssUploadService | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["me"])

    @router.post("/me/avatar/upload-policy", response_model=AvatarUploadCredentialResponse)
    async def avatar_upload_policy(
        payload: AvatarUploadRequest,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        from juya_miniapp_api.shared.errors import AppError

        if uploads is None:
            raise AppError("AVATAR_STORAGE_UNAVAILABLE", "头像上传暂不可用", 503)
        return uploads.create_avatar_upload(user_id, payload.content_type)

    @router.get("/me")
    async def get_me(
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "private, no-store"
        return serialize_me(await service.get_me(user_id))

    @router.patch("/me/profile")
    async def update_profile(
        payload: ProfileUpdate,
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "private, no-store"
        return serialize_me(
            await service.update_profile(
                user_id, payload.nickname, payload.avatar_object_key, clock()
            )
        )

    return router
