from datetime import UTC, datetime, timedelta

import pytest

from juya_miniapp_api.infrastructure.redis.cache import JsonCache
from juya_miniapp_api.integrations.admin_api.schemas import (
    AccessProjection,
    EntitlementProjection,
    LearningCatalog,
    LearningModule,
)
from juya_miniapp_api.modules.learning.catalog_service import CatalogService

NOW = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)


class FakeRedis:
    def __init__(self) -> None:
        # 功能:初始化场景学习的FakeRedis对象的状态存储
        # 参数:
        #     self: 当前场景学习的FakeRedis实例
        # 返回:无返回值。
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, name: str) -> str | None:
        # 功能:读取指定Redis键的缓存内容
        # 参数:
        #     self: 当前场景学习的FakeRedis实例
        #     name: 需要读取或写入的Redis键名称
        # 返回:测试Redis中保存的字符串值; 键不存在时为None
        return self.values.get(name)

    async def set(self, name: str, value: str, *, ex: int) -> None:
        # 功能:写入Redis缓存值并设置过期或条件写入选项
        # 参数:
        #     self: 当前场景学习的FakeRedis实例
        #     name: 需要读取或写入的Redis键名称
        #     value: 写入缓存的序列化字符串或JSON字段映射
        #     ex: Redis键的过期秒数
        # 返回:无返回值。
        self.values[name] = value
        self.ttls[name] = ex

    async def delete(self, *names: str) -> None:
        # 功能:批量删除指定Redis缓存键
        # 参数:
        #     self: 当前场景学习的FakeRedis实例
        #     names: 需要批量删除的Redis键名称
        # 返回:无返回值。
        for name in names:
            self.values.pop(name, None)


class FakeCatalogClient:
    def __init__(self) -> None:
        # 功能:初始化场景学习的FakeCatalogClient对象的状态存储
        # 参数:
        #     self: 当前场景学习的FakeCatalogClient实例
        # 返回:无返回值。
        self.module_calls = 0
        self.catalog_calls = 0

    async def get_modules(self) -> list[LearningModule]:
        # 功能:在测试中获取学习模块定义与目录版本信息
        # 参数:
        #     self: 当前场景学习的FakeCatalogClient实例
        # 返回:学习模块定义集合
        self.module_calls += 1
        return [LearningModule(key="scene_learning", title="场景学习")]

    async def get_catalog(self, user_id: str, summary: object) -> LearningCatalog:
        # 功能:在测试中获取带用户摘要和授权信息的学习目录
        # 参数:
        #     self: 当前场景学习的FakeCatalogClient实例
        #     user_id: 当前操作所属用户的公开标识
        #     summary: 当前用户学习完成数等目录汇总字段
        # 返回:学习目录、用户摘要和权限信息
        del user_id, summary
        self.catalog_calls += 1
        return LearningCatalog(items=[{"scene_id": "scene-1"}])

    async def batch_access(self, user_id: str, scene_ids: object) -> list[AccessProjection]:
        # 功能:在测试中批量向管理端查询用户的场景访问权限
        # 参数:
        #     self: 当前场景学习的FakeCatalogClient实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_ids: 需要批量查询访问权限的场景公开标识序列
        # 返回:用户对场景的访问级别与授权期限集合
        del user_id, scene_ids
        return []

    async def get_entitlements(self, user_id: str) -> EntitlementProjection:
        # 功能:在测试中获取用户当前授权权益投影
        # 参数:
        #     self: 当前场景学习的FakeCatalogClient实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:用户授权权益与到期时间投影
        del user_id
        return EntitlementProjection(
            version="v2",
            limited=[{"expires_at": NOW + timedelta(seconds=8)}],
        )


@pytest.mark.asyncio
async def test_catalog_cache_uses_version_and_never_exceeds_entitlement() -> None:
    # 功能:验证目录缓存按版本隔离且不超过授权期限
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    redis = FakeRedis()
    client = FakeCatalogClient()
    # 匿名函数: clock测试时钟返回固定操作时间以稳定签名与有效期断言
    # 参数:
    #     无形参。
    # 返回: 测试预设的带UTC时区时间
    service = CatalogService(client, JsonCache(redis), clock=lambda: NOW)

    assert len(await service.modules()) == 1
    assert len(await service.modules()) == 1
    first = await service.catalog("user-1", {})
    second = await service.catalog("user-1", {})

    assert client.module_calls == 1
    assert client.catalog_calls == 1
    assert first == second
    assert redis.ttls["juya:cache:learning-catalog:v2:user-1"] == 8
