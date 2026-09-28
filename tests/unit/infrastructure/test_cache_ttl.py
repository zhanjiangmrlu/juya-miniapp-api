from datetime import UTC, datetime, timedelta

import pytest

from juya_miniapp_api.infrastructure.redis.cache import JsonCache, bounded_cache_ttl

NOW = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)


def test_cache_ttl_never_outlives_entitlement() -> None:
    assert bounded_cache_ttl(NOW, 30, NOW + timedelta(seconds=8, milliseconds=900)) == 8
    assert bounded_cache_ttl(NOW, 30, NOW + timedelta(minutes=1)) == 30
    assert bounded_cache_ttl(NOW, 30, NOW) == 0


def test_cache_ttl_without_entitlement_uses_configured_maximum() -> None:
    assert bounded_cache_ttl(NOW, 60, None) == 60


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, name: str) -> str | None:
        return self.values.get(name)

    async def set(self, name: str, value: str, *, ex: int) -> None:
        self.values[name] = value
        self.ttls[name] = ex

    async def delete(self, *names: str) -> None:
        for name in names:
            self.values.pop(name, None)


@pytest.mark.asyncio
async def test_json_cache_is_versioned_and_skips_expired_values() -> None:
    redis = FakeRedis()
    cache = JsonCache(redis)

    assert not await cache.set("home", "user-1", "v1", {"value": 1}, ttl_seconds=0)
    assert await cache.set("home", "user-1", "v1", {"value": 1}, ttl_seconds=12)
    assert await cache.get("home", "user-1", "v1") == {"value": 1}
    assert await cache.get("home", "user-1", "v2") is None
    assert redis.ttls["juya:cache:home:v1:user-1"] == 12

    await cache.invalidate("home", "user-1", "v1")
    assert await cache.get("home", "user-1", "v1") is None
