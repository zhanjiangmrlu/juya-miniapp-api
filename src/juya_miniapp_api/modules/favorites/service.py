import re
import unicodedata
from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any
from urllib.parse import quote

from juya_miniapp_api.modules.favorites.domain import (
    FavoriteEntry,
    FavoriteSource,
    ReviewCompletion,
    ReviewSession,
)
from juya_miniapp_api.modules.favorites.repository import FavoriteRepository
from juya_miniapp_api.modules.learning.access_service import AccessService
from juya_miniapp_api.shared.errors import AppError


def normalize_favorite_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).strip().casefold()
    return re.sub(r"\s+", " ", normalized)


class FavoriteService:
    def __init__(self, repository: FavoriteRepository, access: AccessService | None = None) -> None:
        self._repository = repository
        self._access = access

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
        *,
        revision_id: str | None = None,
        entry_version: int = 1,
        entry_snapshot: dict[str, Any] | None = None,
    ) -> FavoriteEntry:
        if entry_type not in {"VOCABULARY", "PHRASE"}:
            raise AppError("FAVORITE_TYPE_INVALID", "收藏类型无效", 422)
        if self._access is not None:
            if revision_id is None:
                raise AppError("FAVORITE_REVISION_REQUIRED", "收藏须指定发布版本", 422)
            entry = await self._access.get_entry(
                user_id, scene_id, entry_stable_id, revision_id, entry_version, source_locator
            )
            text = entry.english
            sentence_snapshot = entry.sentence_snapshot or entry.english
            if entry.entry_type is not None and entry.entry_type != entry_type:
                raise AppError("FAVORITE_TYPE_INVALID", "收藏类型与词条不符", 422)
            entry_snapshot = entry.model_dump(mode="json")
        normalized_key = normalize_favorite_key(text)
        if not normalized_key or len(normalized_key) > 255:
            raise AppError("FAVORITE_TEXT_INVALID", "收藏内容无效", 422)
        source = FavoriteSource(
            scene_id,
            sentence_snapshot,
            source_locator,
            None,
            revision_id,
            entry_version,
            entry_snapshot or {},
        )
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
                    f"/scenes/{item.scene_id}"
                    + (f"?revision_id={quote(item.revision_id)}" if item.revision_id else "")
                    + f"#{quote(item.source_locator)}"
                    if item.scene_id in accessible_scene_ids
                    else None
                ),
                item.revision_id,
                item.entry_version,
                dict(item.entry_snapshot),
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
        if not card_ids or any(
            not isinstance(card, str) or not card or len(card) > 26 for card in card_ids
        ):
            raise AppError("REVIEW_CARDS_INVALID", "复习须选择有效收藏卡片", 422)
        cards = tuple(str(card) for card in card_ids)
        if len(set(cards)) != len(cards):
            raise AppError("REVIEW_CARDS_INVALID", "复习卡片不能重复", 422)
        return await self._repository.create_review(user_id, cards, idempotency_key, now)

    async def complete_review(
        self,
        user_id: str,
        review_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> ReviewCompletion:
        if not idempotency_key or len(idempotency_key) > 128:
            raise AppError("IDEMPOTENCY_KEY_INVALID", "幂等键无效", 422)
        return await self._repository.complete_review(user_id, review_id, idempotency_key, now)
