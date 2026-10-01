from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from juya_miniapp_api.integrations.admin_api.schemas import AccessProjection
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
        cards = tuple(item.target_id for item in review_cards)
        return TodayTask("FAVORITE_REVIEW", "favorites", cards)
    return history[0] if history else None


class LearningAccess(Protocol):
    async def access(self, user_id: str, scene_ids: Sequence[str]) -> list[AccessProjection]: ...


class LearningService:
    def __init__(
        self, repository: LearningRepository, access: LearningAccess | None = None
    ) -> None:
        self._repository = repository
        self._access = access

    async def _authorize(self, user_id: str, scene_id: str, now: datetime) -> None:
        if self._access is None:
            return
        decisions = await self._access.access(user_id, [scene_id])
        decision = next((item for item in decisions if item.scene_id == scene_id), None)
        if decision is None:
            raise AppError("LEARNING_AUTHORIZATION_PENDING", "学习权益暂时无法确认", 503)
        if decision.level not in {"OPEN", "FORMAL", "LIMITED"}:
            raise AppError("SCENE_ACCESS_DENIED", "无权学习该场景", 403)
        expiry = decision.earliest_expires_at
        if expiry is not None and now >= (expiry if expiry.tzinfo else expiry.replace(tzinfo=UTC)):
            raise AppError("SCENE_ACCESS_DENIED", "场景权益已到期", 403)

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
        await self._authorize(user_id, scene_id, now)
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
        await self._authorize(user_id, scene_id, now)
        return await self._repository.complete(user_id, scene_id, idempotency_key, now)
