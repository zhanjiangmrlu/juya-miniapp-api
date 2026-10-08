from datetime import UTC, datetime
from typing import Any, Protocol

from juya_miniapp_api.integrations.admin_api.client import AdminApiUnavailable
from juya_miniapp_api.integrations.admin_api.schemas import (
    SceneEntry,
    SceneOpenResult,
    SignedMedia,
    SignedResource,
)
from juya_miniapp_api.modules.learning.domain import ReadingPosition
from juya_miniapp_api.modules.learning.repository import SQLAlchemyLearningRepository
from juya_miniapp_api.shared.errors import AppError


class SceneAccessClient(Protocol):
    async def open_scene(
        self, user_id: str, scene_id: str, idempotency_key: str
    ) -> SceneOpenResult:
        # 功能:校验场景访问响应并仅在授权成功后记录打开历史
        # 参数:
        #     self: 当前已发布场景、词条与资源访问客户端实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        # 返回:访问级别与已发布场景内容
        ...

    async def get_signed_media(self, user_id: str, target_id: str) -> SignedMedia:
        # 功能:获取授权有效期内的媒体访问签名链接
        # 参数:
        #     self: 当前已发布场景、词条与资源访问客户端实例
        #     user_id: 当前操作所属用户的公开标识
        #     target_id: 需要学习或签发媒体链接的目标公开标识
        # 返回:授权有效期内的媒体签名链接
        ...

    async def get_entry(
        self,
        user_id: str,
        scene_id: str,
        entry_id: str,
        revision_id: str,
        entry_version: int,
        source_locator: str,
    ) -> SceneEntry:
        # 功能:按发布版本与来源定位查询权威词条内容
        # 参数:
        #     self: 当前已发布场景、词条与资源访问客户端实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     entry_id: 场景中的词条稳定标识
        #     revision_id: 需要访问或固定的场景发布修订标识
        #     entry_version: 固定词条的内容版本号
        #     source_locator: 词条来源在固定场景版本中的定位片段
        # 返回:固定发布版本的权威词条与来源快照
        ...

    async def get_signed_resource(
        self,
        user_id: str,
        scene_id: str,
        resource_id: str,
        revision_id: str,
    ) -> SignedResource:
        # 功能:按场景和发布版本获取资源签名链接
        # 参数:
        #     self: 当前已发布场景、词条与资源访问客户端实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     resource_id: 发布内容中的媒体资源标识
        #     revision_id: 需要访问或固定的场景发布修订标识
        # 返回:固定场景发布版本的资源签名链接
        ...


class OpenHistory(Protocol):
    async def record_open(self, user_id: str, scene_id: str, opened_at: datetime) -> None:
        # 功能:保存用户授权成功打开场景的时间
        # 参数:
        #     self: 当前授权场景打开历史存储实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     opened_at: 用户成功打开授权场景的时间
        # 返回:无返回值。
        ...

    async def list_opened(self, user_id: str) -> list[dict[str, Any]]:
        # 功能:查询用户成功授权打开的场景历史
        # 参数:
        #     self: 当前授权场景打开历史存储实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:用户已授权打开的场景标识与打开时间列表
        ...


class InMemoryOpenHistory:
    def __init__(self) -> None:
        # 功能:初始化场景学习的InMemoryOpenHistory对象的状态存储
        # 参数:
        #     self: 当前场景学习的InMemoryOpenHistory实例
        # 返回:无返回值。
        self.opened: list[tuple[str, str, datetime]] = []

    async def record_open(self, user_id: str, scene_id: str, opened_at: datetime) -> None:
        # 功能:保存用户授权成功打开场景的时间
        # 参数:
        #     self: 当前场景学习的InMemoryOpenHistory实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     opened_at: 用户成功打开授权场景的时间
        # 返回:无返回值。
        self.opened.append((user_id, scene_id, opened_at))

    async def list_opened(self, user_id: str) -> list[dict[str, Any]]:
        # 功能:查询用户成功授权打开的场景历史
        # 参数:
        #     self: 当前场景学习的InMemoryOpenHistory实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:用户已授权打开的场景标识与打开时间列表
        return [
            {"scene_id": scene_id, "opened_at": opened_at}
            for opened_user_id, scene_id, opened_at in self.opened
            if opened_user_id == user_id
        ]


class SQLAlchemyOpenHistory:
    def __init__(self, repository: SQLAlchemyLearningRepository) -> None:
        # 功能:初始化场景学习的SQLAlchemyOpenHistory对象并保存所需依赖与配置
        # 参数:
        #     self: 当前场景学习的SQLAlchemyOpenHistory实例
        #     repository: SQL场景学习进度仓库,承载场景学习业务操作
        # 返回:无返回值。
        self._repository = repository

    async def record_open(self, user_id: str, scene_id: str, opened_at: datetime) -> None:
        # 功能:保存用户授权成功打开场景的时间
        # 参数:
        #     self: 当前场景学习的SQLAlchemyOpenHistory实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     opened_at: 用户成功打开授权场景的时间
        # 返回:无返回值。
        await self._repository.save_progress(
            user_id, scene_id, 0, ReadingPosition("start", 0), opened_at, is_scene_open=True
        )

    async def list_opened(self, user_id: str) -> list[dict[str, Any]]:
        # 功能:查询用户成功授权打开的场景历史
        # 参数:
        #     self: 当前场景学习的SQLAlchemyOpenHistory实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:用户已授权打开的场景标识与打开时间列表
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
        # 功能:初始化已发布场景和媒体访问授权服务并保存所需依赖与配置
        # 参数:
        #     self: 当前已发布场景和媒体访问授权服务实例
        #     client: 已发布场景、词条与资源访问客户端
        #     history: 授权成功后保存场景打开记录的历史仓库
        # 返回:无返回值。
        self._client = client
        self._history = history

    async def open_scene(
        self,
        user_id: str,
        scene_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> SceneOpenResult:
        # 功能:校验场景访问响应并仅在授权成功后记录打开历史
        # 参数:
        #     self: 当前已发布场景和媒体访问授权服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:访问级别与已发布场景内容
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
        if result.access != "PREVIEW":
            await self._history.record_open(user_id, scene_id, now)
        return result

    async def get_signed_media(self, user_id: str, target_id: str, now: datetime) -> SignedMedia:
        # 功能:获取授权有效期内的媒体访问签名链接
        # 参数:
        #     self: 当前已发布场景和媒体访问授权服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     target_id: 需要学习或签发媒体链接的目标公开标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:授权有效期内的媒体签名链接
        try:
            media = await self._client.get_signed_media(user_id, target_id)
        except AdminApiUnavailable as error:
            raise AppError("SIGNED_MEDIA_UNAVAILABLE", "媒体地址暂时不可用", 503) from error
        expires_at = media.expires_at
        normalized = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
        if now >= normalized:
            raise AppError("SIGNED_MEDIA_EXPIRED", "媒体地址已失效", 503)
        return media

    async def get_entry(
        self,
        user_id: str,
        scene_id: str,
        entry_id: str,
        revision_id: str,
        entry_version: int,
        source_locator: str,
    ) -> SceneEntry:
        # 功能:按发布版本与来源定位查询权威词条内容
        # 参数:
        #     self: 当前已发布场景和媒体访问授权服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     entry_id: 场景中的词条稳定标识
        #     revision_id: 需要访问或固定的场景发布修订标识
        #     entry_version: 固定词条的内容版本号
        #     source_locator: 词条来源在固定场景版本中的定位片段
        # 返回:固定发布版本的权威词条与来源快照
        try:
            return await self._client.get_entry(
                user_id, scene_id, entry_id, revision_id, entry_version, source_locator
            )
        except AdminApiUnavailable as error:
            raise AppError("SCENE_ENTRY_UNAVAILABLE", "场景条目暂时不可用", 503) from error

    async def get_signed_resource(
        self,
        user_id: str,
        scene_id: str,
        resource_id: str,
        revision_id: str,
        now: datetime,
    ) -> SignedResource:
        # 功能:按场景和发布版本获取资源签名链接
        # 参数:
        #     self: 当前已发布场景和媒体访问授权服务实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     resource_id: 发布内容中的媒体资源标识
        #     revision_id: 需要访问或固定的场景发布修订标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:固定场景发布版本的资源签名链接
        try:
            result = await self._client.get_signed_resource(
                user_id, scene_id, resource_id, revision_id
            )
        except AdminApiUnavailable as error:
            raise AppError("SIGNED_MEDIA_UNAVAILABLE", "媒体地址暂时不可用", 503) from error
        expiry = result.expires_at
        if now >= (expiry if expiry.tzinfo else expiry.replace(tzinfo=UTC)):
            raise AppError("SIGNED_MEDIA_EXPIRED", "媒体地址已失效", 503)
        return result

    async def open_history(self, user_id: str) -> list[dict[str, Any]]:
        # 功能:返回用户授权成功打开的场景列表
        # 参数:
        #     self: 当前已发布场景和媒体访问授权服务实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:用户已授权打开的场景标识与打开时间列表
        return await self._history.list_opened(user_id)
