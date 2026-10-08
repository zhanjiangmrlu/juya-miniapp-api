from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field

from juya_miniapp_api.infrastructure.observability.metrics import PROGRESS_FAILURES
from juya_miniapp_api.modules.learning.domain import LearningProgress, ReadingPosition
from juya_miniapp_api.modules.learning.repository import SQLAlchemyLearningRepository
from juya_miniapp_api.modules.learning.service import LearningService
from juya_miniapp_api.modules.users.router import UserDependency
from juya_miniapp_api.shared.errors import AppError


class PositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_sequence: int = Field(ge=0)
    entry_id: str = Field(min_length=1, max_length=64)
    offset: int = Field(default=0, ge=0)


def _progress(value: LearningProgress) -> dict[str, object]:
    # 功能:序列化场景学习进度与阅读位置
    # 参数:
    #     value: 待转换的场景阅读位置、请求序号与学习时间
    # 返回:场景标识、阅读位置、客户端序号与开始完成时间
    return {
        "scene_id": value.scene_id,
        "source_type": value.source_type,
        "position": {
            "entry_id": value.position.entry_id,
            "offset": value.position.offset,
        },
        "client_sequence": value.client_sequence,
        "started_at": value.started_at,
        "completed_at": value.completed_at,
        "last_learned_at": value.last_learned_at,
    }


# 匿名函数: clock默认时钟在调用时读取当前UTC时间
# 参数:
#     无形参。
# 返回: 带UTC时区的当前时间
def create_learning_router(
    service: LearningService,
    repository: SQLAlchemyLearningRepository,
    *,
    user_dependency: UserDependency,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    achievement_reader: Callable[[str], Awaitable[dict[str, int]]] | None = None,
) -> APIRouter:
    # 功能:创建并绑定场景学习路由与业务依赖
    # 参数:
    #     service: 场景授权与学习进度服务,承载场景学习业务操作
    #     repository: SQL场景学习进度仓库,承载场景学习业务操作
    #     user_dependency: 校验登录凭证并取得当前用户标识的FastAPI依赖
    #     clock: 提供当前时间的可替换时钟回调
    #     achievement_reader: 按用户读取学习成就统计的异步回调
    # 返回:包含业务端点的FastAPI路由器
    router = APIRouter(prefix="/api/v1", tags=["learning"])

    @router.put("/scenes/{scene_id}/progress")
    async def save_progress(
        scene_id: str,
        payload: PositionRequest,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        # 功能:按客户端序号保存场景阅读位置并拒绝旧请求覆盖
        # 参数:
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     payload: 已校验的客户端进度序号与阅读位置
        #     user_id: 当前操作所属用户的公开标识
        # 返回:场景阅读位置、客户端序号及学习时间
        try:
            result = await service.save_progress(
                user_id,
                scene_id,
                payload.client_sequence,
                ReadingPosition(payload.entry_id, payload.offset),
                clock(),
            )
        except AppError as error:
            PROGRESS_FAILURES.labels(code=error.code).inc()
            raise
        except Exception:
            PROGRESS_FAILURES.labels(code="INTERNAL_ERROR").inc()
            raise
        return _progress(result)

    @router.post("/scenes/{scene_id}/complete")
    async def complete(
        scene_id: str,
        user_id: Annotated[str, Depends(user_dependency)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, object]:
        # 功能:幂等完成场景学习并记录完成事件与北京时间打卡
        # 参数:
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     user_id: 当前操作所属用户的公开标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        # 返回:场景进度、是否首次完成和北京时间打卡日期
        result = await service.complete(user_id, scene_id, idempotency_key, clock())
        return {
            "progress": _progress(result.progress),
            "created": result.created,
            "checkin_date": result.checkin_date,
        }

    @router.get("/scenes/{scene_id}/result")
    async def result(
        scene_id: str,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object] | None:
        # 功能:返回当前场景进度及用户学习成就
        # 参数:
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     user_id: 当前操作所属用户的公开标识
        # 返回:场景进度与用户学习成就统计;原记录不存在时为None
        progress = await repository.get_progress(user_id, scene_id)
        if progress is None:
            return None
        payload = _progress(progress)
        if achievement_reader is not None:
            payload.update(await achievement_reader(user_id))
        return payload

    @router.get("/history/scenes")
    async def history(
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        # 功能:列出当前用户场景学习历史
        # 参数:
        #     user_id: 当前操作所属用户的公开标识
        # 返回:场景学习历史列表与分页标记
        items = await repository.list_history(user_id)
        return {"items": [_progress(item) for item in items], "has_more": False}

    return router
