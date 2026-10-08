import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol


def bounded_cache_ttl(
    now: datetime, maximum_seconds: int, entitlement_expires_at: datetime | None
) -> int:
    # 功能:计算不超过授权剩余期限的缓存有效秒数
    # 参数:
    #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
    #     maximum_seconds: 目录缓存配置的最长有效秒数
    #     entitlement_expires_at: 用户授权到期时间,缓存不得晚于该时刻失效
    # 返回:不超过配置上限与授权期限的缓存秒数,过期时为0
    if maximum_seconds <= 0:
        return 0
    if entitlement_expires_at is None:
        return maximum_seconds
    remaining = int((entitlement_expires_at - now).total_seconds())
    return max(0, min(maximum_seconds, remaining))


class RedisCacheClient(Protocol):
    async def get(self, name: str) -> Any:
        # 功能:读取指定Redis键的缓存内容
        # 参数:
        #     self: 当前Redis缓存读写接口实例
        #     name: 需要读取或写入的Redis键名称
        # 返回:Redis客户端对应命令的原始返回值
        ...

    async def set(self, name: str, value: str, *, ex: int) -> Any:
        # 功能:写入Redis缓存值并设置过期或条件写入选项
        # 参数:
        #     self: 当前Redis缓存读写接口实例
        #     name: 需要读取或写入的Redis键名称
        #     value: 写入缓存的序列化字符串或JSON字段映射
        #     ex: Redis键的过期秒数
        # 返回:Redis客户端对应命令的原始返回值
        ...

    async def delete(self, *names: str) -> Any:
        # 功能:批量删除指定Redis缓存键
        # 参数:
        #     self: 当前Redis缓存读写接口实例
        #     names: 需要批量删除的Redis键名称
        # 返回:Redis客户端对应命令的原始返回值
        ...


class JsonCache:
    def __init__(self, redis: RedisCacheClient, key_prefix: str = "juya:cache") -> None:
        # 功能:初始化分版本JSON缓存服务并保存所需依赖与配置
        # 参数:
        #     self: 当前分版本JSON缓存服务实例
        #     redis: 提供缓存、原子脚本或随机数防重放操作的Redis客户端
        #     key_prefix: Redis缓存、随机数或限流键的隔离前缀
        # 返回:无返回值。
        self._redis = redis
        self._key_prefix = key_prefix

    def key(self, namespace: str, identifier: str, version: str) -> str:
        # 功能:生成包含命名空间、版本和业务标识的缓存键
        # 参数:
        #     self: 当前分版本JSON缓存服务实例
        #     namespace: 缓存业务分区或上传对象所属目录
        #     identifier: 缓存对象的业务标识
        #     version: 缓存内容的结构或发布版本标识
        # 返回:按命名空间或时间窗口隔离的Redis键
        return f"{self._key_prefix}:{namespace}:{version}:{identifier}"

    async def get(self, namespace: str, identifier: str, version: str) -> dict[str, object] | None:
        # 功能:读取指定版本的JSON缓存对象
        # 参数:
        #     self: 当前分版本JSON缓存服务实例
        #     namespace: 缓存业务分区或上传对象所属目录
        #     identifier: 缓存对象的业务标识
        #     version: 缓存内容的结构或发布版本标识
        # 返回:已解析的JSON缓存字段;未命中或结构无效时为None
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
        # 功能:保存JSON对象并设置缓存有效期
        # 参数:
        #     self: 当前分版本JSON缓存服务实例
        #     namespace: 缓存业务分区或上传对象所属目录
        #     identifier: 缓存对象的业务标识
        #     version: 缓存内容的结构或发布版本标识
        #     value: 写入缓存的序列化字符串或JSON字段映射
        #     ttl_seconds: Redis缓存或防重放记录的存活秒数
        # 返回:JSON缓存是否成功写入;非正有效期返回False
        if ttl_seconds <= 0:
            return False
        await self._redis.set(
            self.key(namespace, identifier, version),
            json.dumps(dict(value), ensure_ascii=False, separators=(",", ":")),
            ex=ttl_seconds,
        )
        return True

    async def invalidate(self, namespace: str, identifier: str, version: str) -> None:
        # 功能:删除指定命名空间、业务标识和版本的缓存
        # 参数:
        #     self: 当前分版本JSON缓存服务实例
        #     namespace: 缓存业务分区或上传对象所属目录
        #     identifier: 缓存对象的业务标识
        #     version: 缓存内容的结构或发布版本标识
        # 返回:无返回值。
        await self._redis.delete(self.key(namespace, identifier, version))
