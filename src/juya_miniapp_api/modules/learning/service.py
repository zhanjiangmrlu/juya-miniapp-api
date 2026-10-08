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
    # 功能:按继续学习、新场景、收藏复习、历史场景顺序选择今日任务
    # 参数:
    #     accessible_unfinished: 有权继续学习但尚未完成的场景候选
    #     accessible_new: 当前用户有权访问且尚未学习的新场景候选
    #     review_cards: 可供今日任务选择的收藏复习候选
    #     history: 历史场景候选,前序任务均不可用时作为兜底
    # 返回:按优先级选择的今日学习或复习任务;不存在或无候选时返回None
    if accessible_unfinished:
        return accessible_unfinished[0]
    if accessible_new:
        return accessible_new[0]
    if review_cards:
        cards = tuple(item.target_id for item in review_cards)
        return TodayTask("FAVORITE_REVIEW", "favorites", cards)
    return history[0] if history else None


class LearningAccess(Protocol):
    async def access(self, user_id: str, scene_ids: Sequence[str]) -> list[AccessProjection]:
        # 功能:批量查询场景访问权限投影
        # 参数:
        #     self: 当前场景学习权限查询接口实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_ids: 需要批量查询访问权限的场景公开标识序列
        # 返回:用户对场景的访问级别与授权期限集合
        ...


class LearningService:
    def __init__(
        self, repository: LearningRepository, access: LearningAccess | None = None
    ) -> None:
        # 功能:初始化场景授权与学习进度服务并保存所需依赖与配置
        # 参数:
        #     self: 当前场景授权与学习进度服务实例
        #     repository: 场景阅读进度与完成事件仓库,承载场景学习业务操作
        #     access: 校验场景权限并解析已发布内容的访问服务
        # 返回:无返回值。
        self._repository = repository
        self._access = access

    async def _authorize(self, user_id: str, scene_id: str, now: datetime) -> None:
        # 功能:校验场景学习权限并拒绝预览或失效授权
        # 参数:
        #     self: 当前场景授权与学习进度服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:无返回值。
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
        # 功能:按客户端序号保存场景阅读位置并拒绝旧请求覆盖
        # 参数:
        #     self: 当前场景授权与学习进度服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     client_sequence: 客户端递增的进度请求序号,防止旧位置覆盖新位置
        #     position: 当前场景词条标识与阅读偏移位置
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:场景阅读位置、请求序号与学习时间
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
        # 功能:幂等完成场景学习并记录完成事件与北京时间打卡
        # 参数:
        #     self: 当前场景授权与学习进度服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:学习进度、首次完成标记与打卡日期
        if not idempotency_key or len(idempotency_key) > 128:
            raise AppError("IDEMPOTENCY_KEY_INVALID", "幂等键无效", 422)
        await self._authorize(user_id, scene_id, now)
        return await self._repository.complete(user_id, scene_id, idempotency_key, now)
