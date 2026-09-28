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


def create_internal_accounts_router(
    service: AccountLifecycleService,
    *,
    current_service: ServiceDependency,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(prefix="/internal/v1", tags=["internal-accounts"])

    @router.post("/users/{user_id}/deletion-cleanup-result")
    async def record_cleanup_result(
        user_id: str,
        payload: CleanupResultRequest,
        response: Response,
        _principal: Annotated[ServicePrincipal, Depends(current_service)],
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        result = await service.record_cross_domain_cleanup(
            user_id,
            payload.deletion_request_id,
            succeeded=payload.succeeded,
            now=clock(),
        )
        return serialize_deletion(result)

    return router
