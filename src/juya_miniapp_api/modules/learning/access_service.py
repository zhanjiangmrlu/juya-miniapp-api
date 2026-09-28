from datetime import UTC, datetime
from typing import Any, Protocol

from juya_miniapp_api.integrations.admin_api.client import AdminApiUnavailable
from juya_miniapp_api.integrations.admin_api.schemas import (
    SceneEntry,
    SceneOpenResult,
    SignedMedia,
)
from juya_miniapp_api.modules.learning.domain import ReadingPosition
from juya_miniapp_api.modules.learning.repository import SQLAlchemyLearningRepository
from juya_miniapp_api.shared.errors import AppError


class SceneAccessClient(Protocol):
    async def open_scene(
        self, user_id: str, scene_id: str, idempotency_key: str
    ) -> SceneOpenResult: ...

    async def get_signed_media(self, user_id: str, target_id: str) -> SignedMedia: ...

    async def get_entry(self, user_id: str, scene_id: str, entry_id: str) -> SceneEntry: ...


class OpenHistory(Protocol):
    async def record_open(self, user_id: str, scene_id: str, opened_at: datetime) -> None: ...

    async def list_opened(self, user_id: str) -> list[dict[str, Any]]: ...


class InMemoryOpenHistory:
    def __init__(self) -> None:
        self.opened: list[tuple[str, str, datetime]] = []

    async def record_open(self, user_id: str, scene_id: str, opened_at: datetime) -> None:
        self.opened.append((user_id, scene_id, opened_at))

    async def list_opened(self, user_id: str) -> list[dict[str, Any]]:
        return [
            {"scene_id": scene_id, "opened_at": opened_at}
            for opened_user_id, scene_id, opened_at in self.opened
            if opened_user_id == user_id
        ]


class SQLAlchemyOpenHistory:
    def __init__(self, repository: SQLAlchemyLearningRepository) -> None:
        self._repository = repository

    async def record_open(self, user_id: str, scene_id: str, opened_at: datetime) -> None:
        await self._repository.save_progress(
            user_id, scene_id, 0, ReadingPosition("start", 0), opened_at
        )

    async def list_opened(self, user_id: str) -> list[dict[str, Any]]:
        history = await self._repository.list_history(user_id)
        return [
            {
                "scene_id": item.scene_id,
                "opened_at": item.started_at,
                "last_learned_at": item.last_learned_at,
                "completed_at": item.completed_at,
            }
            for item in history
        ]


class AccessService:
    def __init__(self, client: SceneAccessClient, history: OpenHistory) -> None:
        self._client = client
        self._history = history

    async def open_scene(
        self,
        user_id: str,
        scene_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> SceneOpenResult:
        try:
            result = await self._client.open_scene(user_id, scene_id, idempotency_key)
        except AdminApiUnavailable:
            return SceneOpenResult(authorization_pending=True)
        if result.scene is None or result.access is None:
            return SceneOpenResult(authorization_pending=True)
        if result.earliest_expires_at is not None:
            expires_at = result.earliest_expires_at
            normalized = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
            if now >= normalized:
                return SceneOpenResult(authorization_pending=True)
        await self._history.record_open(user_id, scene_id, now)
        return result

    async def get_signed_media(self, user_id: str, target_id: str, now: datetime) -> SignedMedia:
        try:
            media = await self._client.get_signed_media(user_id, target_id)
        except AdminApiUnavailable as error:
            raise AppError("SIGNED_MEDIA_UNAVAILABLE", "媒体地址暂时不可用", 503) from error
        expires_at = media.expires_at
        normalized = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
        if now >= normalized:
            raise AppError("SIGNED_MEDIA_EXPIRED", "媒体地址已失效", 503)
        return media

    async def get_entry(self, user_id: str, scene_id: str, entry_id: str) -> SceneEntry:
        try:
            return await self._client.get_entry(user_id, scene_id, entry_id)
        except AdminApiUnavailable as error:
            raise AppError("SCENE_ENTRY_UNAVAILABLE", "场景条目暂时不可用", 503) from error

    async def open_history(self, user_id: str) -> list[dict[str, Any]]:
        return await self._history.list_opened(user_id)
