from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field

from juya_miniapp_api.infrastructure.observability.metrics import PROGRESS_FAILURES
from juya_miniapp_api.modules.learning.domain import LearningProgress, ReadingPosition
from juya_miniapp_api.modules.learning.repository import SQLAlchemyLearningRepository
from juya_miniapp_api.modules.learning.service import LearningService
from juya_miniapp_api.modules.users.router import UserDependency
from juya_miniapp_api.shared.errors import AppError


class PositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_sequence: int = Field(ge=0)
    entry_id: str = Field(min_length=1, max_length=64)
    offset: int = Field(default=0, ge=0)


def _progress(value: LearningProgress) -> dict[str, object]:
    return {
        "scene_id": value.scene_id,
        "source_type": value.source_type,
        "position": {
            "entry_id": value.position.entry_id,
            "offset": value.position.offset,
        },
        "client_sequence": value.client_sequence,
        "started_at": value.started_at,
        "completed_at": value.completed_at,
        "last_learned_at": value.last_learned_at,
    }


def create_learning_router(
    service: LearningService,
    repository: SQLAlchemyLearningRepository,
    *,
    user_dependency: UserDependency,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    achievement_reader: Callable[[str], Awaitable[dict[str, int]]] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["learning"])

    @router.put("/scenes/{scene_id}/progress")
    async def save_progress(
        scene_id: str,
        payload: PositionRequest,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        try:
            result = await service.save_progress(
                user_id,
                scene_id,
                payload.client_sequence,
                ReadingPosition(payload.entry_id, payload.offset),
                clock(),
            )
        except AppError as error:
            PROGRESS_FAILURES.labels(code=error.code).inc()
            raise
        except Exception:
            PROGRESS_FAILURES.labels(code="INTERNAL_ERROR").inc()
            raise
        return _progress(result)

    @router.post("/scenes/{scene_id}/complete")
    async def complete(
        scene_id: str,
        user_id: Annotated[str, Depends(user_dependency)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, object]:
        result = await service.complete(user_id, scene_id, idempotency_key, clock())
        return {
            "progress": _progress(result.progress),
            "created": result.created,
            "checkin_date": result.checkin_date,
        }

    @router.get("/scenes/{scene_id}/result")
    async def result(
        scene_id: str,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object] | None:
        progress = await repository.get_progress(user_id, scene_id)
        if progress is None:
            return None
        payload = _progress(progress)
        if achievement_reader is not None:
            payload.update(await achievement_reader(user_id))
        return payload

    @router.get("/history/scenes")
    async def history(
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        items = await repository.list_history(user_id)
        return {"items": [_progress(item) for item in items], "has_more": False}

    return router
