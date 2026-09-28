from collections.abc import Sequence
from datetime import datetime

from juya_miniapp_api.modules.learning.domain import (
    CompletionResult,
    LearningProgress,
    ReadingPosition,
    TodayTask,
)
from juya_miniapp_api.modules.learning.repository import LearningRepository
from juya_miniapp_api.shared.errors import AppError


def select_today_task(
    accessible_unfinished: Sequence[TodayTask],
    accessible_new: Sequence[TodayTask],
    review_cards: Sequence[TodayTask],
    history: Sequence[TodayTask],
) -> TodayTask | None:
    if accessible_unfinished:
        return accessible_unfinished[0]
    if accessible_new:
        return accessible_new[0]
    if review_cards:
        cards = tuple(item.target_id for item in review_cards[:10])
        return TodayTask("FAVORITE_REVIEW", "favorites", cards)
    return history[0] if history else None


class LearningService:
    def __init__(self, repository: LearningRepository) -> None:
        self._repository = repository

    async def save_progress(
        self,
        user_id: str,
        scene_id: str,
        client_sequence: int,
        position: ReadingPosition,
        now: datetime,
    ) -> LearningProgress:
        if client_sequence < 0 or position.offset < 0:
            raise AppError("LEARNING_POSITION_INVALID", "学习进度无效", 422)
        return await self._repository.save_progress(
            user_id, scene_id, client_sequence, position, now
        )

    async def complete(
        self,
        user_id: str,
        scene_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> CompletionResult:
        if not idempotency_key or len(idempotency_key) > 128:
            raise AppError("IDEMPOTENCY_KEY_INVALID", "幂等键无效", 422)
        return await self._repository.complete(user_id, scene_id, idempotency_key, now)
