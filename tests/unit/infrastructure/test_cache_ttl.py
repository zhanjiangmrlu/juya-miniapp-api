from datetime import UTC, datetime, timedelta

import pytest

from juya_miniapp_api.infrastructure.redis.cache import JsonCache, bounded_cache_ttl

NOW = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)


def test_cache_ttl_never_outlives_entitlement() -> None:
    # 功能:验证缓存有效期不超过授权到期时间
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    assert bounded_cache_ttl(NOW, 30, NOW + timedelta(seconds=8, milliseconds=900)) == 8
    assert bounded_cache_ttl(NOW, 30, NOW + timedelta(minutes=1)) == 30
    assert bounded_cache_ttl(NOW, 30, NOW) == 0


def test_cache_ttl_without_entitlement_uses_configured_maximum() -> None:
    # 功能:验证无授权期限时缓存使用配置的最大有效期
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    assert bounded_cache_ttl(NOW, 60, None) == 60


class FakeRedis:
    def __init__(self) -> None:
        # 功能:初始化小程序的FakeRedis对象的状态存储
        # 参数:
        #     self: 当前小程序的FakeRedis实例
        # 返回:无返回值。
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, name: str) -> str | None:
        # 功能:读取指定Redis键的缓存内容
        # 参数:
        #     self: 当前小程序的FakeRedis实例
        #     name: 需要读取或写入的Redis键名称
        # 返回:测试Redis中保存的字符串值; 键不存在时为None
        return self.values.get(name)

    async def set(self, name: str, value: str, *, ex: int) -> None:
        # 功能:写入Redis缓存值并设置过期或条件写入选项
        # 参数:
        #     self: 当前小程序的FakeRedis实例
        #     name: 需要读取或写入的Redis键名称
        #     value: 写入缓存的序列化字符串或JSON字段映射
        #     ex: Redis键的过期秒数
        # 返回:无返回值。
        self.values[name] = value
        self.ttls[name] = ex

    async def delete(self, *names: str) -> None:
        # 功能:批量删除指定Redis缓存键
        # 参数:
        #     self: 当前小程序的FakeRedis实例
        #     names: 需要批量删除的Redis键名称
        # 返回:无返回值。
        for name in names:
            self.values.pop(name, None)


@pytest.mark.asyncio
async def test_json_cache_is_versioned_and_skips_expired_values() -> None:
    # 功能:验证JSON缓存按版本隔离且不写入已过期内容
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    redis = FakeRedis()
    cache = JsonCache(redis)

    assert not await cache.set("home", "user-1", "v1", {"value": 1}, ttl_seconds=0)
    assert await cache.set("home", "user-1", "v1", {"value": 1}, ttl_seconds=12)
    assert await cache.get("home", "user-1", "v1") == {"value": 1}
    assert await cache.get("home", "user-1", "v2") is None
    assert redis.ttls["juya:cache:home:v1:user-1"] == 12

    await cache.invalidate("home", "user-1", "v1")
    assert await cache.get("home", "user-1", "v1") is None
