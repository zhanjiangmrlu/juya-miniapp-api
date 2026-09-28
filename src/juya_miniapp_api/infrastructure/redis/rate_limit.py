import asyncio
from datetime import datetime, timedelta
from typing import Any, Protocol

from juya_miniapp_api.shared.errors import AppError


class RedisScriptClient(Protocol):
    async def eval(self, script: str, numkeys: int, *keys_and_args: object) -> Any: ...


class RateLimiter(Protocol):
    async def allow(
        self,
        scope: str,
        subject: str,
        *,
        limit: int,
        window: timedelta,
        now: datetime,
    ) -> bool: ...


async def enforce_rate_limit(
    limiter: RateLimiter | None,
    scope: str,
    subject: str,
    *,
    limit: int,
    window: timedelta,
    now: datetime,
) -> None:
    if limiter is not None and not await limiter.allow(
        scope, subject, limit=limit, window=window, now=now
    ):
        raise AppError("RATE_LIMITED", "请求过于频繁 请稍后再试", 429)


_FIXED_WINDOW_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


class RedisRateLimiter:
    def __init__(self, redis: RedisScriptClient, key_prefix: str = "juya:rate") -> None:
        self._redis = redis
        self._key_prefix = key_prefix

    def key(self, scope: str, subject: str, now: datetime, window: timedelta) -> str:
        seconds = max(1, int(window.total_seconds()))
        bucket = int(now.timestamp()) // seconds
        return f"{self._key_prefix}:{scope}:{subject}:{bucket}"

    async def allow(
        self,
        scope: str,
        subject: str,
        *,
        limit: int,
        window: timedelta,
        now: datetime,
    ) -> bool:
        if limit < 1:
            return False
        seconds = max(1, int(window.total_seconds()))
        count = await self._redis.eval(
            _FIXED_WINDOW_SCRIPT,
            1,
            self.key(scope, subject, now, window),
            seconds,
        )
        return int(count) <= limit


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._counts: dict[tuple[str, str, int], int] = {}
        self._lock = asyncio.Lock()

    async def allow(
        self,
        scope: str,
        subject: str,
        *,
        limit: int,
        window: timedelta,
        now: datetime,
    ) -> bool:
        if limit < 1:
            return False
        seconds = max(1, int(window.total_seconds()))
        key = (scope, subject, int(now.timestamp()) // seconds)
        async with self._lock:
            count = self._counts.get(key, 0) + 1
            self._counts[key] = count
            return count <= limit
