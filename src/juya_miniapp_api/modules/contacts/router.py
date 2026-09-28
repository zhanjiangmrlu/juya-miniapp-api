from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from juya_miniapp_api.infrastructure.redis.rate_limit import RateLimiter, enforce_rate_limit
from juya_miniapp_api.modules.contacts.service import ContactService
from juya_miniapp_api.modules.users.router import UserDependency, _contact
from juya_miniapp_api.modules.users.schemas import ContactSaveRequest, CorrectionCreateRequest
from juya_miniapp_api.shared.errors import AppError


def create_contacts_router(
    service: ContactService,
    *,
    user_dependency: UserDependency,
    rate_limiter: RateLimiter | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/me/contact", tags=["contact"])

    @router.get("")
    async def get_contact(
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object] | None:
        response.headers["Cache-Control"] = "private, no-store"
        return _contact(await service.get(user_id))

    @router.put("")
    async def save_contact(
        payload: ContactSaveRequest,
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object] | None:
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
        await service.withdraw(user_id, clock())

    @router.post("/corrections", status_code=201)
    async def create_correction(
        payload: CorrectionCreateRequest,
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        correction = await service.request_correction(user_id, payload.reason, clock())
        response.headers["Cache-Control"] = "private, no-store"
        return {
            "id": correction.public_id,
            "status": correction.status,
            "created_at": correction.created_at,
        }

    return router
