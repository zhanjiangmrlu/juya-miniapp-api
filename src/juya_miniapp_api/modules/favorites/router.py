from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field

from juya_miniapp_api.modules.favorites.domain import FavoriteEntry
from juya_miniapp_api.modules.favorites.repository import SQLAlchemyFavoriteRepository
from juya_miniapp_api.modules.favorites.service import FavoriteService
from juya_miniapp_api.modules.learning.catalog_service import CatalogService
from juya_miniapp_api.modules.users.router import UserDependency


class FavoriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_type: str
    text: str = Field(min_length=1, max_length=255)
    entry_stable_id: str = Field(min_length=1, max_length=64)
    revision_id: str = Field(min_length=1, max_length=26)
    entry_version: int = Field(ge=1)
    scene_id: str = Field(min_length=1, max_length=64)
    sentence_snapshot: str = ""
    source_locator: str = Field(min_length=1, max_length=228)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card_ids: list[str]


def _favorite(value: FavoriteEntry) -> dict[str, object]:
    # 功能:序列化收藏记录、固定版本来源和可访问链接
    # 参数:
    #     value: 待转换的收藏记录及固定版本来源快照
    # 返回:收藏类型、标准化英文、学习时间与固定版本来源列表
    return {
        "id": value.public_id,
        "entry_type": value.entry_type,
        "normalized_key": value.normalized_key,
        "entry_stable_id": value.entry_stable_id,
        "favorited_at": value.favorited_at,
        "last_reviewed_at": value.last_reviewed_at,
        "sources": [
            {
                "scene_id": item.scene_id,
                "sentence_snapshot": item.sentence_snapshot,
                "source_locator": item.source_locator,
                "original_link": item.original_link,
                "revision_id": item.revision_id,
                "entry_version": item.entry_version,
                "entry_snapshot": item.entry_snapshot,
            }
            for item in value.sources
        ],
    }


# 匿名函数: clock默认时钟在调用时读取当前UTC时间
# 参数:
#     无形参。
# 返回: 带UTC时区的当前时间
def create_favorites_router(
    service: FavoriteService,
    repository: SQLAlchemyFavoriteRepository,
    *,
    user_dependency: UserDependency,
    catalog: CatalogService | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    # 功能:创建并绑定收藏复习路由与业务依赖
    # 参数:
    #     service: 收藏及复习业务服务,承载收藏复习业务操作
    #     repository: SQL收藏与复习仓库,承载收藏复习业务操作
    #     user_dependency: 校验登录凭证并取得当前用户标识的FastAPI依赖
    #     catalog: 读取学习目录和场景权限的业务服务
    #     clock: 提供当前时间的可替换时钟回调
    # 返回:包含业务端点的FastAPI路由器
    router = APIRouter(prefix="/api/v1", tags=["favorites"])

    @router.get("/favorites")
    async def favorites(
        user_id: Annotated[str, Depends(user_dependency)],
        cursor: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> dict[str, object]:
        # 功能:分页列出当前用户收藏
        # 参数:
        #     user_id: 当前操作所属用户的公开标识
        #     cursor: 分页游标;空值从第一页开始
        #     limit: 本次查询或任务领取允许的最大记录数量
        # 返回:收藏列表、下一页游标与是否还有后续页
        items = await repository.list_favorites(user_id, after_id=cursor, limit=limit + 1)
        visible = items[:limit]
        return {
            "items": [_favorite(item) for item in visible],
            "next_cursor": visible[-1].public_id if len(items) > limit else None,
            "has_more": len(items) > limit,
        }

    @router.get("/reviews/queue")
    async def review_queue(
        user_id: Annotated[str, Depends(user_dependency)],
        cursor: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> dict[str, object]:
        # 功能:返回当前用户收藏复习候选队列
        # 参数:
        #     user_id: 当前操作所属用户的公开标识
        #     cursor: 分页游标;空值从第一页开始
        #     limit: 本次查询或任务领取允许的最大记录数量
        # 返回:可复习收藏列表、下一页游标与是否还有后续页
        items = await repository.list_favorites(user_id, after_id=cursor, limit=limit + 1)
        visible = items[:limit]
        return {
            "items": [_favorite(item) for item in visible],
            "next_cursor": visible[-1].public_id if len(items) > limit else None,
            "has_more": len(items) > limit,
        }

    @router.post("/favorites", status_code=201)
    async def create_favorite(
        payload: FavoriteRequest,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        # 功能:创建收藏并固定发布版本、词条内容和来源快照
        # 参数:
        #     payload: 已校验的收藏词条类别、发布版本与来源定位
        #     user_id: 当前操作所属用户的公开标识
        # 返回:新建或合并后的收藏记录与固定版本来源快照
        return _favorite(
            await service.favorite(
                user_id,
                payload.entry_type,
                payload.text,
                payload.entry_stable_id,
                payload.scene_id,
                payload.sentence_snapshot,
                payload.source_locator,
                clock(),
                revision_id=payload.revision_id,
                entry_version=payload.entry_version,
            )
        )

    @router.get("/favorites/{favorite_id}")
    async def favorite_detail(
        favorite_id: str,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        # 功能:读取收藏详情并生成有权访问的来源链接
        # 参数:
        #     favorite_id: 当前用户收藏记录的公开标识
        #     user_id: 当前操作所属用户的公开标识
        # 返回:收藏内容、固定发布版本来源与有权打开的原文链接
        accessible_scene_ids: set[str] = set()
        favorite = await repository.get(user_id, favorite_id)
        if favorite is not None and catalog is not None:
            projections = await catalog.access(
                user_id, [source.scene_id for source in favorite.sources]
            )
            accessible_scene_ids = {
                item.scene_id for item in projections if item.level in {"OPEN", "FORMAL", "LIMITED"}
            }
        return _favorite(
            await service.detail(
                user_id,
                favorite_id,
                accessible_scene_ids=accessible_scene_ids,
            )
        )

    @router.delete("/favorites/{favorite_id}", status_code=204)
    async def delete_favorite(
        favorite_id: str,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> None:
        # 功能:删除当前用户收藏及关联来源
        # 参数:
        #     favorite_id: 当前用户收藏记录的公开标识
        #     user_id: 当前操作所属用户的公开标识
        # 返回:无返回值。
        await service.delete(user_id, favorite_id)

    @router.post("/reviews", status_code=201)
    async def create_review(
        payload: ReviewRequest,
        user_id: Annotated[str, Depends(user_dependency)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, object]:
        # 功能:校验并固定用户选择的收藏卡片生成复习会话
        # 参数:
        #     payload: 已校验的待复习的收藏卡片标识
        #     user_id: 当前操作所属用户的公开标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        # 返回:复习会话标识、类型、开始时间、卡片数量与固定卡片标识
        review = await service.create_review(user_id, payload.card_ids, idempotency_key, clock())
        return {
            "id": review.id,
            "card_count": review.card_count,
            "card_ids": review.card_ids,
            "started_at": review.started_at,
        }

    @router.post("/reviews/{review_id}/complete")
    async def complete_review(
        review_id: str,
        user_id: Annotated[str, Depends(user_dependency)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, object]:
        # 功能:幂等完成复习并更新所选收藏的复习时间与打卡
        # 参数:
        #     review_id: 收藏复习会话的公开标识
        #     user_id: 当前操作所属用户的公开标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        # 返回:复习会话标识、首次完成标记与北京时间打卡日期
        result = await service.complete_review(user_id, review_id, idempotency_key, clock())
        return {
            "id": result.session.id,
            "created": result.created,
            "completed_at": result.session.completed_at,
            "checkin_date": result.checkin_date,
        }

    return router
