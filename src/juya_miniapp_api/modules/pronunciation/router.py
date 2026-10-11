from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from juya_miniapp_api.modules.pronunciation.domain import (
    PronunciationQuery,
    PronunciationRequest,
    PronunciationResponse,
)
from juya_miniapp_api.modules.pronunciation.service import PronunciationService
from juya_miniapp_api.modules.users.router import UserDependency


def create_pronunciation_router(
    service: PronunciationService,
    *,
    user_dependency: UserDependency,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/pronunciations", tags=["pronunciation"])

    @router.post("/{target_id}/resolve", response_model=PronunciationResponse)
    async def resolve(
        target_id: str,
        payload: PronunciationRequest,
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> PronunciationResponse:
        response.headers["Cache-Control"] = "private, no-store"
        return await service.resolve(user_id, target_id, payload)

    @router.get("/tasks/{task_id}", response_model=PronunciationResponse)
    async def poll(
        task_id: str,
        payload: Annotated[PronunciationQuery, Query()],
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> PronunciationResponse:
        response.headers["Cache-Control"] = "private, no-store"
        reference = PronunciationRequest(**payload.model_dump(exclude={"target_id"}))
        return await service.poll(user_id, task_id, reference, payload.target_id)

    return router
