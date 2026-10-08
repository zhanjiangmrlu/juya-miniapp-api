import hashlib
import hmac
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import SecretStr
from starlette.requests import Request

from juya_miniapp_api.shared.errors import AppError

INTERNAL_SIGNATURE_MAX_SKEW_SECONDS = 300
INTERNAL_NONCE_TTL_SECONDS = 300


@dataclass(frozen=True, slots=True)
class ServicePrincipal:
    service_name: str


class NonceStore(Protocol):
    def use_once(self, service_name: str, nonce: str, ttl_seconds: int) -> Awaitable[bool]:
        # 功能:原子记录内部签名随机数以阻止请求重放
        # 参数:
        #     self: 当前内部签名随机数防重放存储实例
        #     service_name: 健康检查或签名随机数隔离使用的服务名称
        #     nonce: 内部请求签名的单次随机数
        #     ttl_seconds: Redis缓存或防重放记录的存活秒数
        # 返回:异步完成后的随机数首次登记状态
        ...


class RedisSetClient(Protocol):
    async def set(
        self,
        name: str,
        value: str,
        *,
        ex: int,
        nx: bool,
    ) -> Any:
        # 功能:写入Redis缓存值并设置过期或条件写入选项
        # 参数:
        #     self: 当前Redis条件写入接口实例
        #     name: 需要检查的Docker容器名称
        #     value: 写入缓存的序列化字符串或JSON字段映射
        #     ex: Redis键的过期秒数
        #     nx: 是否只在Redis键尚不存在时写入
        # 返回:Redis客户端对应命令的原始返回值
        ...


class RedisNonceStore:
    def __init__(self, redis: RedisSetClient, key_prefix: str = "juya:internal:nonce") -> None:
        # 功能:初始化小程序的RedisNonceStore对象并保存所需依赖与配置
        # 参数:
        #     self: 当前小程序的RedisNonceStore实例
        #     redis: 提供缓存、原子脚本或随机数防重放操作的Redis客户端
        #     key_prefix: Redis缓存、随机数或限流键的隔离前缀
        # 返回:无返回值。
        self._redis = redis
        self._key_prefix = key_prefix

    async def use_once(self, service_name: str, nonce: str, ttl_seconds: int) -> bool:
        # 功能:原子记录内部签名随机数以阻止请求重放
        # 参数:
        #     self: 当前小程序的RedisNonceStore实例
        #     service_name: 健康检查或签名随机数隔离使用的服务名称
        #     nonce: 内部请求签名的单次随机数
        #     ttl_seconds: Redis缓存或防重放记录的存活秒数
        # 返回:随机数是否首次成功登记;False表示已被使用
        key = f"{self._key_prefix}:{service_name}:{nonce}"
        return bool(await self._redis.set(key, "1", ex=ttl_seconds, nx=True))


def sign_request(
    method: str,
    path_with_query: str,
    timestamp: int,
    nonce: str,
    body: bytes,
    secret: bytes,
) -> str:
    # 功能:计算覆盖方法、路径、时间戳、随机数和请求体的内部HMAC签名
    # 参数:
    #     method: 参与请求发送和签名的HTTP方法
    #     path_with_query: 参与HMAC签名的原始路径与完整查询串
    #     timestamp: 参与内部HMAC签名的Unix秒级时间戳
    #     nonce: 内部请求签名的单次随机数
    #     body: 参与内部请求签名的原始请求体字节
    #     secret: 内部服务请求HMAC签名和验签的共享密钥
    # 返回:内部HMAC签名的十六进制字符串
    body_hash = hashlib.sha256(body).hexdigest()
    canonical = "\n".join(
        (method.upper(), path_with_query, str(timestamp), nonce, body_hash)
    ).encode()
    return hmac.new(secret, canonical, hashlib.sha256).hexdigest()


def _path_with_query(request: Request) -> str:
    # 功能:提取请求的原始路径与查询串作为签名输入
    # 参数:
    #     request: FastAPI请求对象
    # 返回:原始路径和查询串组成的签名文本
    raw_path = request.scope.get("raw_path", request.url.path.encode())
    path = bytes(raw_path).decode("ascii")
    query = bytes(request.scope.get("query_string", b"")).decode("ascii")
    return f"{path}?{query}" if query else path


def _unauthorized(code: str, message: str, *, status_code: int = 401) -> AppError:
    # 功能:构造内部接口鉴权失败的业务异常
    # 参数:
    #     code: 对客户端公开的业务错误码
    #     message: 允许向客户端显示的错误说明
    #     status_code: 对客户端返回的HTTP错误状态码
    # 返回:携带错误码和安全说明的业务异常
    return AppError(code, message, status_code)


async def verify_request_signature(
    request: Request,
    secret: SecretStr,
    nonce_store: NonceStore,
    now: datetime,
) -> ServicePrincipal:
    # 功能:校验内部请求时间、HMAC签名与随机数防重放
    # 参数:
    #     request: FastAPI请求对象
    #     secret: 内部服务请求HMAC签名和验签的共享密钥
    #     nonce_store: 记录已用签名随机数以拒绝重放的存储服务
    #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
    # 返回:已验证的内部调用服务身份
    service_name = request.headers.get("X-Juya-Service", "")
    timestamp_value = request.headers.get("X-Juya-Timestamp", "")
    nonce = request.headers.get("X-Juya-Nonce", "")
    provided_signature = request.headers.get("X-Juya-Signature", "")
    if not service_name or not timestamp_value or not nonce or not provided_signature:
        raise _unauthorized("INVALID_INTERNAL_SIGNATURE", "内部请求签名无效")
    try:
        timestamp = int(timestamp_value)
    except ValueError as error:
        raise _unauthorized("INVALID_INTERNAL_SIGNATURE", "内部请求签名无效") from error
    normalized_now = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    if abs(int(normalized_now.timestamp()) - timestamp) > INTERNAL_SIGNATURE_MAX_SKEW_SECONDS:
        raise _unauthorized("INTERNAL_SIGNATURE_EXPIRED", "内部请求签名时间戳已过期")
    body = await request.body()
    expected = sign_request(
        request.method,
        _path_with_query(request),
        timestamp,
        nonce,
        body,
        secret.get_secret_value().encode(),
    )
    if not hmac.compare_digest(provided_signature, expected):
        raise _unauthorized("INVALID_INTERNAL_SIGNATURE", "内部请求签名无效")
    if not await nonce_store.use_once(service_name, nonce, INTERNAL_NONCE_TTL_SECONDS):
        raise _unauthorized("INTERNAL_REQUEST_REPLAYED", "内部请求已处理", status_code=409)
    return ServicePrincipal(service_name)
