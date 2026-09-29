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
    response.headers["Cache-Control"] = "no-store"


def _search_item(view: MeView) -> dict[str, object]:
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


def create_internal_users_router(
    users: UserService,
    contacts: ContactService,
    learning_overviews: LearningOverviewRepository,
    *,
    service_dependency: ServiceDependency,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(prefix="/internal/v1", tags=["internal-users"])

    @router.post("/users/search")
    async def search_users(
        payload: UserSearchRequest,
        response: Response,
        _principal: Annotated[ServicePrincipal, Depends(service_dependency)],
        _admin_id: Annotated[str, Header(alias="X-Admin-Id")],
    ) -> dict[str, object]:
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
        raise AppError("METHOD_NOT_ALLOWED", "用户搜索只允许 POST", 405)

    @router.post("/users/contact-projections")
    async def contact_projections(
        payload: ContactProjectionRequest,
        response: Response,
        _principal: Annotated[ServicePrincipal, Depends(service_dependency)],
        _admin_id: Annotated[str, Header(alias="X-Admin-Id")],
    ) -> dict[str, object]:
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
        _no_store(response)
        return serialize_me(await users.get_me(user_id))

    @router.get("/users/{user_id}/learning-overview")
    async def learning_overview(
        user_id: str,
        response: Response,
        _principal: Annotated[ServicePrincipal, Depends(service_dependency)],
        _admin_id: Annotated[str, Header(alias="X-Admin-Id")],
    ) -> dict[str, int]:
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
