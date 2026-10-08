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


# 匿名函数: clock默认时钟在调用时读取当前UTC时间
# 参数:
#     无形参。
# 返回: 带UTC时区的当前时间
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
    # 功能:创建并绑定打卡首页路由与业务依赖
    # 参数:
    #     repository: SQL用户打卡日期仓库,承载学习打卡业务操作
    #     user_dependency: 校验登录凭证并取得当前用户标识的FastAPI依赖
    #     learning: 读取场景进度与学习历史的SQL仓库
    #     catalog: 读取学习目录和场景权限的业务服务
    #     messages: 查询站内消息与未读数量的业务服务
    #     favorites: 读取收藏和复习队列的SQL仓库
    #     clock: 提供当前时间的可替换时钟回调
    # 返回:包含业务端点的FastAPI路由器
    router = APIRouter(prefix="/api/v1", tags=["checkins"])

    @router.get("/me/checkins/summary")
    async def summary(
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, int]:
        # 功能:返回用户累计与连续打卡统计
        # 参数:
        #     user_id: 当前操作所属用户的公开标识
        # 返回:当前连续、累计与最长连续打卡天数
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
        # 功能:汇总首页问候、打卡统计、今日任务与未读消息数量
        # 参数:
        #     user_id: 当前操作所属用户的公开标识
        # 返回:首页问候、打卡统计、优先级选定的今日任务与未读消息数量
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
                    if item.level in {"OPEN", "FORMAL", "LIMITED"}
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
                cursor = None
                while True:
                    batch = await favorites.list_favorites(user_id, after_id=cursor, limit=100)
                    review_cards.extend(
                        TodayTask("FAVORITE_CARD", item.public_id) for item in batch
                    )
                    if len(batch) < 100:
                        break
                    cursor = batch[-1].public_id
            today_task = select_today_task(unfinished, new_scenes, review_cards, completed)
        task_payload = None
        if today_task is not None:
            task_payload = {
                "kind": today_task.kind,
                "target_id": today_task.target_id,
                "card_ids": list(today_task.card_ids),
                "review_queue_url": "/api/v1/reviews/queue"
                if today_task.kind == "FAVORITE_REVIEW"
                else None,
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
    # 功能:按北京时间时段选择首页问候语
    # 参数:
    #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
    # 返回:对应北京时间时段的中文问候语
    hour = now.hour
    if hour < 12:
        return "早上好"
    if hour < 18:
        return "下午好"
    return "晚上好"
