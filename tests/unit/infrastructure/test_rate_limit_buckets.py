from datetime import UTC, datetime, timedelta

import pytest

from juya_miniapp_api.infrastructure.redis.rate_limit import InMemoryRateLimiter

NOW = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_rate_limit_buckets_are_independent() -> None:
    limiter = InMemoryRateLimiter()

    assert await limiter.allow("login", "client-1", limit=2, window=timedelta(minutes=1), now=NOW)
    assert await limiter.allow("login", "client-1", limit=2, window=timedelta(minutes=1), now=NOW)
    assert not await limiter.allow(
        "login", "client-1", limit=2, window=timedelta(minutes=1), now=NOW
    )
    assert await limiter.allow(
        "feedback", "client-1", limit=1, window=timedelta(minutes=1), now=NOW
    )
    assert await limiter.allow(
        "login", "client-1", limit=2, window=timedelta(minutes=1), now=NOW + timedelta(minutes=1)
    )
