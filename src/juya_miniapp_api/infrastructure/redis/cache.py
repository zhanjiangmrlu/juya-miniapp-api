import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol


def bounded_cache_ttl(
    now: datetime, maximum_seconds: int, entitlement_expires_at: datetime | None
) -> int:
    if maximum_seconds <= 0:
        return 0
    if entitlement_expires_at is None:
        return maximum_seconds
    remaining = int((entitlement_expires_at - now).total_seconds())
    return max(0, min(maximum_seconds, remaining))


class RedisCacheClient(Protocol):
    async def get(self, name: str) -> Any: ...

    async def set(self, name: str, value: str, *, ex: int) -> Any: ...

    async def delete(self, *names: str) -> Any: ...


class JsonCache:
    def __init__(self, redis: RedisCacheClient, key_prefix: str = "juya:cache") -> None:
        self._redis = redis
        self._key_prefix = key_prefix

    def key(self, namespace: str, identifier: str, version: str) -> str:
        return f"{self._key_prefix}:{namespace}:{version}:{identifier}"

    async def get(self, namespace: str, identifier: str, version: str) -> dict[str, object] | None:
        raw = await self._redis.get(self.key(namespace, identifier, version))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        value = json.loads(raw)
        return value if isinstance(value, dict) else None

    async def set(
        self,
        namespace: str,
        identifier: str,
        version: str,
        value: Mapping[str, object],
        *,
        ttl_seconds: int,
    ) -> bool:
        if ttl_seconds <= 0:
            return False
        await self._redis.set(
            self.key(namespace, identifier, version),
            json.dumps(dict(value), ensure_ascii=False, separators=(",", ":")),
            ex=ttl_seconds,
        )
        return True

    async def invalidate(self, namespace: str, identifier: str, version: str) -> None:
        await self._redis.delete(self.key(namespace, identifier, version))
