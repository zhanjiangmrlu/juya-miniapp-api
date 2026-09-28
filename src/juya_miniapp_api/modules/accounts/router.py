from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict

from juya_miniapp_api.modules.accounts.domain import DeletionRequest
from juya_miniapp_api.modules.accounts.service import AccountLifecycleService

UserDependency = Callable[[], Awaitable[str]]


class ClearLearningDataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: str


def serialize_deletion(request: DeletionRequest) -> dict[str, object]:
    return {
        "id": request.id,
        "status": request.status,
        "requested_at": request.requested_at,
        "effective_at": request.effective_at,
        "revoked_at": request.revoked_at,
        "completed_at": request.completed_at,
    }


def create_accounts_router(
    service: AccountLifecycleService,
    *,
    user_dependency: UserDependency,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/me", tags=["account-privacy"])

    @router.delete("/learning-data", status_code=204)
    async def clear_learning_data(
        payload: ClearLearningDataRequest,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> None:
        await service.clear_learning_data(user_id, payload.confirmation)

    @router.post("/deletion", status_code=202)
    async def request_deletion(
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        return serialize_deletion(await service.request_deletion(user_id, clock()))

    @router.post("/deletion/revoke")
    async def revoke_deletion(
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        return serialize_deletion(await service.revoke_deletion(user_id, clock()))

    return router
