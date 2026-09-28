from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from juya_miniapp_api.modules.checkins.repository import SQLAlchemyCheckinRepository
from juya_miniapp_api.modules.checkins.service import (
    beijing_learning_date,
    summarize_checkins,
)
from juya_miniapp_api.modules.favorites.repository import SQLAlchemyFavoriteRepository
from juya_miniapp_api.modules.learning.catalog_service import CatalogService
from juya_miniapp_api.modules.learning.domain import TodayTask
from juya_miniapp_api.modules.learning.repository import SQLAlchemyLearningRepository
from juya_miniapp_api.modules.learning.service import select_today_task
from juya_miniapp_api.modules.messages.service import MessageService
from juya_miniapp_api.modules.users.router import UserDependency


def create_checkins_router(
    repository: SQLAlchemyCheckinRepository,
    *,
    user_dependency: UserDependency,
    learning: SQLAlchemyLearningRepository | None = None,
    catalog: CatalogService | None = None,
    messages: MessageService | None = None,
    favorites: SQLAlchemyFavoriteRepository | None = None,
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
        today_task: TodayTask | None = None
        if learning is not None:
            history = await learning.list_history(user_id)
            catalog_scene_ids: list[str] = []
            if catalog is not None:
                catalog_result = await catalog.catalog(user_id, {})
                for item in catalog_result.items:
                    candidate = item.get("scene_id") or item.get("public_id") or item.get("id")
                    if isinstance(candidate, str):
                        catalog_scene_ids.append(candidate)
            all_scene_ids = list(
                dict.fromkeys([item.scene_id for item in history] + catalog_scene_ids)
            )
            accessible = set(all_scene_ids)
            if catalog is not None and all_scene_ids:
                projections = await catalog.access(user_id, all_scene_ids)
                accessible = {
                    item.scene_id
                    for item in projections
                    if item.level not in {"NONE", "NO_ACCESS", "DENIED", "EXPIRED"}
                }
            unfinished = [
                TodayTask("CONTINUE_SCENE", item.scene_id)
                for item in history
                if item.completed_at is None and item.scene_id in accessible
            ]
            completed = [
                TodayTask("HISTORY_SCENE", item.scene_id)
                for item in history
                if item.completed_at is not None and item.scene_id in accessible
            ]
            history_ids = {item.scene_id for item in history}
            new_scenes = [
                TodayTask("NEW_SCENE", scene_id)
                for scene_id in catalog_scene_ids
                if scene_id in accessible and scene_id not in history_ids
            ]
            review_cards: list[TodayTask] = []
            if favorites is not None:
                review_cards = [
                    TodayTask("FAVORITE_CARD", item.public_id)
                    for item in await favorites.list_favorites(user_id, limit=10)
                ]
            today_task = select_today_task(unfinished, new_scenes, review_cards, completed)
        task_payload = None
        if today_task is not None:
            task_payload = {
                "kind": today_task.kind,
                "target_id": today_task.target_id,
                "card_ids": list(today_task.card_ids),
            }
        return {
            "greeting": _greeting(clock()),
            "checkins": {
                "current_streak": result.current_streak,
                "total_days": result.total_days,
                "longest_streak": result.longest_streak,
            },
            "today_task": task_payload,
            "unread_message_count": (
                await messages.unread_count(user_id) if messages is not None else 0
            ),
        }

    return router


def _greeting(now: datetime) -> str:
    hour = now.hour
    if hour < 12:
        return "早上好"
    if hour < 18:
        return "下午好"
    return "晚上好"
