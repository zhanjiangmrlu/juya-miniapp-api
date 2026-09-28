import re
import unicodedata
from collections.abc import Collection, Sequence
from datetime import datetime

from juya_miniapp_api.modules.favorites.domain import (
    FavoriteEntry,
    FavoriteSource,
    ReviewCompletion,
    ReviewSession,
)
from juya_miniapp_api.modules.favorites.repository import FavoriteRepository
from juya_miniapp_api.shared.errors import AppError


def normalize_favorite_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).strip().casefold()
    return re.sub(r"\s+", " ", normalized)


class FavoriteService:
    def __init__(self, repository: FavoriteRepository) -> None:
        self._repository = repository

    async def favorite(
        self,
        user_id: str,
        entry_type: str,
        text: str,
        entry_stable_id: str,
        scene_id: str,
        sentence_snapshot: str,
        source_locator: str,
        now: datetime,
    ) -> FavoriteEntry:
        if entry_type not in {"VOCABULARY", "PHRASE"}:
            raise AppError("FAVORITE_TYPE_INVALID", "收藏类型无效", 422)
        normalized_key = normalize_favorite_key(text)
        if not normalized_key or len(normalized_key) > 255:
            raise AppError("FAVORITE_TEXT_INVALID", "收藏内容无效", 422)
        source = FavoriteSource(scene_id, sentence_snapshot, source_locator)
        return await self._repository.upsert(
            user_id,
            entry_type,
            normalized_key,
            entry_stable_id,
            source,
            now,
        )

    async def detail(
        self,
        user_id: str,
        favorite_id: str,
        *,
        accessible_scene_ids: Collection[str],
    ) -> FavoriteEntry:
        favorite = await self._repository.get(user_id, favorite_id)
        if favorite is None:
            raise AppError("FAVORITE_NOT_FOUND", "收藏不存在", 404)
        sources = tuple(
            FavoriteSource(
                item.scene_id,
                item.sentence_snapshot,
                item.source_locator,
                (
                    f"/scenes/{item.scene_id}#{item.source_locator}"
                    if item.scene_id in accessible_scene_ids
                    else None
                ),
            )
            for item in favorite.sources
        )
        return FavoriteEntry(
            favorite.public_id,
            favorite.user_id,
            favorite.entry_type,
            favorite.normalized_key,
            favorite.entry_stable_id,
            favorite.favorited_at,
            favorite.last_reviewed_at,
            sources,
        )

    async def delete(self, user_id: str, favorite_id: str) -> None:
        await self._repository.delete(user_id, favorite_id)

    async def create_review(
        self,
        user_id: str,
        card_ids: Sequence[object],
        idempotency_key: str,
        now: datetime,
    ) -> ReviewSession:
        if not idempotency_key or len(idempotency_key) > 128:
            raise AppError("IDEMPOTENCY_KEY_INVALID", "幂等键无效", 422)
        return await self._repository.create_review(user_id, len(card_ids), idempotency_key, now)

    async def complete_review(
        self,
        user_id: str,
        review_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> ReviewCompletion:
        if not idempotency_key:
            raise AppError("IDEMPOTENCY_KEY_INVALID", "幂等键无效", 422)
        return await self._repository.complete_review(user_id, review_id, idempotency_key, now)
