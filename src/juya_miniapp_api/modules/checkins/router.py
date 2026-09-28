from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from juya_miniapp_api.modules.checkins.repository import SQLAlchemyCheckinRepository
from juya_miniapp_api.modules.checkins.service import (
    beijing_learning_date,
    summarize_checkins,
)
from juya_miniapp_api.modules.users.router import UserDependency


def create_checkins_router(
    repository: SQLAlchemyCheckinRepository,
    *,
    user_dependency: UserDependency,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["checkins"])

    @router.get("/me/checkins/summary")
    async def summary(
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, int]:
        result = summarize_checkins(
            await repository.list_days(user_id), today=beijing_learning_date(clock())
        )
        return {
            "current_streak": result.current_streak,
            "total_days": result.total_days,
            "longest_streak": result.longest_streak,
        }

    @router.get("/home")
    async def home(
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        result = summarize_checkins(
            await repository.list_days(user_id), today=beijing_learning_date(clock())
        )
        return {
            "checkins": {
                "current_streak": result.current_streak,
                "total_days": result.total_days,
                "longest_streak": result.longest_streak,
            },
            "today_task": None,
        }

    return router
