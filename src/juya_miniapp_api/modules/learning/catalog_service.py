from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from typing import Protocol

from juya_miniapp_api.infrastructure.redis.cache import JsonCache
from juya_miniapp_api.integrations.admin_api.client import AdminApiUnavailable
from juya_miniapp_api.integrations.admin_api.schemas import (
    AccessProjection,
    EntitlementProjection,
    LearningCatalog,
    LearningFeatures,
    LearningModule,
)
from juya_miniapp_api.modules.learning.stage import LearningStageReader
from juya_miniapp_api.shared.errors import AppError


class CatalogFeaturesReader(Protocol):
    async def learning_features(self) -> LearningFeatures: ...


class CatalogClient(Protocol):
    async def get_modules(self) -> list[LearningModule]:
        # 功能:获取学习模块定义与目录版本信息
        # 参数:
        #     self: 当前管理端学习目录查询客户端实例
        # 返回:学习模块定义集合
        ...

    async def get_catalog(self, user_id: str, summary: Mapping[str, object]) -> LearningCatalog:
        # 功能:获取带用户摘要和授权信息的学习目录
        # 参数:
        #     self: 当前管理端学习目录查询客户端实例
        #     user_id: 当前操作所属用户的公开标识
        #     summary: 当前用户学习完成数等目录汇总字段
        # 返回:学习目录、用户摘要和权限信息
        ...

    async def batch_access(self, user_id: str, scene_ids: Sequence[str]) -> list[AccessProjection]:
        # 功能:批量向管理端查询用户的场景访问权限
        # 参数:
        #     self: 当前管理端学习目录查询客户端实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_ids: 需要批量查询访问权限的场景公开标识序列
        # 返回:用户对场景的访问级别与授权期限集合
        ...

    async def get_entitlements(self, user_id: str) -> EntitlementProjection:
        # 功能:获取用户当前授权权益投影
        # 参数:
        #     self: 当前管理端学习目录查询客户端实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:用户授权权益与到期时间投影
        ...


class CatalogService:
    # 匿名函数: clock默认时钟在调用时读取当前UTC时间
    # 参数:
    #     无形参。
    # 返回: 带UTC时区的当前时间
    def __init__(
        self,
        client: CatalogClient,
        cache: JsonCache | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        *,
        stage_reader: LearningStageReader | None = None,
        features: CatalogFeaturesReader | None = None,
    ) -> None:
        # 功能:初始化学习目录与授权缓存服务并保存所需依赖与配置
        # 参数:
        #     self: 当前学习目录与授权缓存服务实例
        #     client: 管理端学习目录查询客户端
        #     cache: 带业务命名空间和版本的JSON缓存服务
        #     clock: 提供当前时间的可替换时钟回调
        # 返回:无返回值。
        self._client = client
        self._cache = cache
        self._clock = clock
        self._stage_reader = stage_reader
        self._features = features

    async def modules(self) -> list[LearningModule]:
        # 功能:返回可用学习模块列表
        # 参数:
        #     self: 当前学习目录与授权缓存服务实例
        # 返回:学习模块定义集合
        try:
            return await self._client.get_modules()
        except AdminApiUnavailable:
            return []

    async def catalog(self, user_id: str, summary: Mapping[str, object]) -> LearningCatalog:
        # Authorization, module switches and publication can change independently of
        # entitlement version. Always request the current authoritative projection.
        try:
            result = await self._client.get_catalog(user_id, summary)
            enabled = False
            if not result.authorization_pending and self._features is not None:
                with suppress(AppError):
                    enabled = (await self._features.learning_features()).profile_completion_enabled
            result = result.model_copy(update={"profile_completion_enabled": enabled})
            if not result.authorization_pending and self._stage_reader is not None:
                facts = await self._stage_reader.get(user_id, self._clock())
                return result.model_copy(update={"user_stage": facts.stage})
            return result
        except AdminApiUnavailable:
            return LearningCatalog(authorization_pending=True)

    async def access(self, user_id: str, scene_ids: Sequence[str]) -> list[AccessProjection]:
        # 功能:批量查询场景访问权限投影
        # 参数:
        #     self: 当前学习目录与授权缓存服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_ids: 需要批量查询访问权限的场景公开标识序列
        # 返回:用户对场景的访问级别与授权期限集合
        try:
            return await self._client.batch_access(user_id, scene_ids)
        except AdminApiUnavailable:
            return []

    async def entitlements(self, user_id: str) -> EntitlementProjection:
        # 功能:读取用户当前的场景授权与到期时间投影
        # 参数:
        #     self: 当前学习目录与授权缓存服务实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:用户授权权益与到期时间投影
        try:
            return await self._client.get_entitlements(user_id)
        except AdminApiUnavailable:
            return EntitlementProjection(authorization_pending=True)
