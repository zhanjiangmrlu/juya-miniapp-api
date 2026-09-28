import os
from datetime import UTC, datetime, timedelta

import pytest
from redis.asyncio import Redis

from juya_miniapp_api.infrastructure.redis.rate_limit import RedisRateLimiter


@pytest.mark.asyncio
async def test_redis_rate_limit_is_atomic_and_scoped() -> None:
    redis_url = os.environ.get("JUYA_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("JUYA_TEST_REDIS_URL is required for Redis integration tests")
    redis = Redis.from_url(redis_url, decode_responses=True)
    limiter = RedisRateLimiter(redis, key_prefix="test:juya:rate")
    now = datetime.now(UTC)
    try:
        await redis.delete(limiter.key("login", "client-1", now, timedelta(minutes=1)))
        results = [
            await limiter.allow("login", "client-1", limit=2, window=timedelta(minutes=1), now=now)
            for _ in range(3)
        ]
        assert results == [True, True, False]
    finally:
        await redis.delete(limiter.key("login", "client-1", now, timedelta(minutes=1)))
        await redis.aclose()
