from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from juya_miniapp_api.infrastructure.security.service_hmac import ServicePrincipal
from juya_miniapp_api.modules.contacts.service import ContactService
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


class CorrectionDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str


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


def create_internal_users_router(
    users: UserService,
    contacts: ContactService,
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

    @router.get("/users/{user_id}")
    async def user_detail(
        user_id: str,
        response: Response,
        _principal: Annotated[ServicePrincipal, Depends(service_dependency)],
        _admin_id: Annotated[str, Header(alias="X-Admin-Id")],
    ) -> dict[str, object]:
        _no_store(response)
        return serialize_me(await users.get_me(user_id))

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

    @router.post("/contact-corrections/{correction_id}/decision")
    async def decide_correction(
        correction_id: str,
        payload: CorrectionDecisionRequest,
        response: Response,
        _principal: Annotated[ServicePrincipal, Depends(service_dependency)],
        admin_id: Annotated[str, Header(alias="X-Admin-Id")],
    ) -> dict[str, object]:
        _no_store(response)
        correction = await contacts.decide_correction(
            correction_id, payload.decision, admin_id, clock()
        )
        return {
            "id": correction.public_id,
            "status": correction.status,
            "processed_at": correction.processed_at,
        }

    return router
