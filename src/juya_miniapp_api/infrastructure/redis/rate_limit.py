import asyncio
from datetime import datetime, timedelta
from typing import Any, Protocol

from juya_miniapp_api.shared.errors import AppError


class RedisScriptClient(Protocol):
    async def eval(self, script: str, numkeys: int, *keys_and_args: object) -> Any:
        # 功能:执行Redis Lua脚本以保证限流计数原子性
        # 参数:
        #     self: 当前Redis原子脚本接口实例
        #     script: 保证计数原子性的Redis Lua脚本内容
        #     numkeys: Lua脚本参数列表中Redis键的数量
        #     keys_and_args: Lua脚本按Redis接口约定传入的键与参数序列
        # 返回:Redis原子限流脚本的执行返回值
        ...


class RateLimiter(Protocol):
    async def allow(
        self,
        scope: str,
        subject: str,
        *,
        limit: int,
        window: timedelta,
        now: datetime,
    ) -> bool:
        # 功能:按业务范围、主体和时间窗口判断是否允许本次请求
        # 参数:
        #     self: 当前按业务范围和主体的请求限流服务实例
        #     scope: 隔离限流配额的业务操作范围
        #     subject: 限流统计主体,如用户、设备或业务键
        #     limit: 当前时间窗口允许的最大请求数量
        #     window: 限流计数的固定时间窗口长度
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:本次请求是否仍在当前限流窗口配额内
        ...


async def enforce_rate_limit(
    limiter: RateLimiter | None,
    scope: str,
    subject: str,
    *,
    limit: int,
    window: timedelta,
    now: datetime,
) -> None:
    # 功能:检查请求频率并在超过业务配额时抛出限流异常
    # 参数:
    #     limiter: 可选的业务请求限流服务; 未配置时跳过限流
    #     scope: 隔离限流配额的业务操作范围
    #     subject: 限流统计主体,如用户、设备或业务键
    #     limit: 当前时间窗口允许的最大请求数量
    #     window: 限流计数的固定时间窗口长度
    #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
    # 返回:无返回值。
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
        # 功能:初始化小程序的RedisRateLimiter对象并保存所需依赖与配置
        # 参数:
        #     self: 当前小程序的RedisRateLimiter实例
        #     redis: 提供缓存、原子脚本或随机数防重放操作的Redis客户端
        #     key_prefix: Redis缓存、随机数或限流键的隔离前缀
        # 返回:无返回值。
        self._redis = redis
        self._key_prefix = key_prefix

    def key(self, scope: str, subject: str, now: datetime, window: timedelta) -> str:
        # 功能:生成包含时间窗口、主体和业务范围的限流键
        # 参数:
        #     self: 当前小程序的RedisRateLimiter实例
        #     scope: 隔离限流配额的业务操作范围
        #     subject: 限流统计主体,如用户、设备或业务键
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        #     window: 限流计数的固定时间窗口长度
        # 返回:按命名空间或时间窗口隔离的Redis键
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
        # 功能:按业务范围、主体和时间窗口判断是否允许本次请求
        # 参数:
        #     self: 当前小程序的RedisRateLimiter实例
        #     scope: 隔离限流配额的业务操作范围
        #     subject: 限流统计主体,如用户、设备或业务键
        #     limit: 当前时间窗口允许的最大请求数量
        #     window: 限流计数的固定时间窗口长度
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:本次请求是否仍在当前限流窗口配额内
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
        # 功能:初始化小程序的InMemoryRateLimiter对象的状态存储
        # 参数:
        #     self: 当前小程序的InMemoryRateLimiter实例
        # 返回:无返回值。
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
        # 功能:按业务范围、主体和时间窗口判断是否允许本次请求
        # 参数:
        #     self: 当前小程序的InMemoryRateLimiter实例
        #     scope: 隔离限流配额的业务操作范围
        #     subject: 限流统计主体,如用户、设备或业务键
        #     limit: 当前时间窗口允许的最大请求数量
        #     window: 限流计数的固定时间窗口长度
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:本次请求是否仍在当前限流窗口配额内
        if limit < 1:
            return False
        seconds = max(1, int(window.total_seconds()))
        key = (scope, subject, int(now.timestamp()) // seconds)
        async with self._lock:
            count = self._counts.get(key, 0) + 1
            self._counts[key] = count
            return count <= limit
