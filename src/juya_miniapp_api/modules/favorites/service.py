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
    # 功能:统一收藏文本的Unicode、大小写与空白以实现去重
    # 参数:
    #     text: 收藏英文词条原文,标准化后作为去重键
    # 返回:用于收藏去重的Unicode、大小写和空白统一文本
    normalized = unicodedata.normalize("NFKC", text).strip().casefold()
    return re.sub(r"\s+", " ", normalized)


class FavoriteService:
    def __init__(self, repository: FavoriteRepository, access: AccessService | None = None) -> None:
        # 功能:初始化收藏及复习业务服务并保存所需依赖与配置
        # 参数:
        #     self: 当前收藏及复习业务服务实例
        #     repository: 收藏与复习会话仓库,承载收藏复习业务操作
        #     access: 校验场景权限并解析已发布内容的访问服务
        # 返回:无返回值。
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
        # 功能:校验收藏内容并合并同一标准化词条的来源快照
        # 参数:
        #     self: 当前收藏及复习业务服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     entry_type: 收藏词条类别,区分单词与短语
        #     text: 收藏英文词条原文,标准化后作为去重键
        #     entry_stable_id: 跨内容修订保持稳定的词条标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     sentence_snapshot: 收藏时固定的来源语句内容
        #     source_locator: 词条来源在固定场景版本中的定位片段
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        #     revision_id: 需要访问或固定的场景发布修订标识
        #     entry_version: 固定词条的内容版本号
        #     entry_snapshot: 收藏时固定的词条展示与发音内容快照
        # 返回:收藏记录及固定版本来源快照
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
        # 功能:读取当前用户收藏并按场景授权生成来源跳转链接
        # 参数:
        #     self: 当前收藏及复习业务服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     favorite_id: 当前用户收藏记录的公开标识
        #     accessible_scene_ids: 当前用户有权打开的场景公开标识集合
        # 返回:收藏记录及固定版本来源快照
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
        # 功能:删除当前用户收藏与关联来源
        # 参数:
        #     self: 当前收藏及复习业务服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     favorite_id: 当前用户收藏记录的公开标识
        # 返回:无返回值。
        await self._repository.delete(user_id, favorite_id)

    async def create_review(
        self,
        user_id: str,
        card_ids: Sequence[object],
        idempotency_key: str,
        now: datetime,
    ) -> ReviewSession:
        # 功能:校验并固定用户选择的收藏卡片生成复习会话
        # 参数:
        #     self: 当前收藏及复习业务服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     card_ids: 本次复习选择的收藏公开标识序列
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:固定所选收藏卡片的复习会话
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
        # 功能:幂等完成复习并更新所选收藏的复习时间与打卡
        # 参数:
        #     self: 当前收藏及复习业务服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     review_id: 收藏复习会话的公开标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:复习完成状态与打卡日期
        if not idempotency_key or len(idempotency_key) > 128:
            raise AppError("IDEMPOTENCY_KEY_INVALID", "幂等键无效", 422)
        return await self._repository.complete_review(user_id, review_id, idempotency_key, now)
