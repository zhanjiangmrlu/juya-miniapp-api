import json
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from juya_miniapp_api.infrastructure.observability.metrics import ADMIN_API_LATENCY
from juya_miniapp_api.infrastructure.observability.request_id import get_traceparent
from juya_miniapp_api.infrastructure.security.service_hmac import sign_request
from juya_miniapp_api.integrations.admin_api.schemas import (
    AccessProjection,
    EntitlementProjection,
    LearningCatalog,
    LearningModule,
    SceneEntry,
    SceneOpenResult,
    SignedMedia,
    SignedResource,
)
from juya_miniapp_api.shared.errors import AppError


def _validate[ModelType: BaseModel](model: type[ModelType], payload: object) -> ModelType:
    # 功能:使用指定数据模型校验管理端响应结构
    # 参数:
    #     model: 用于校验上游响应的Pydantic模型类型
    #     payload: 待按指定Pydantic模型校验的上游响应内容
    # 返回:通过指定Pydantic模型校验的数据对象
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise AdminApiUnavailable() from error


class AdminApiUnavailable(AppError):
    def __init__(self) -> None:
        # 功能:初始化小程序的AdminApiUnavailable对象的状态存储
        # 参数:
        #     self: 当前小程序的AdminApiUnavailable实例
        # 返回:无返回值。
        super().__init__("ADMIN_API_UNAVAILABLE", "内容与权益服务暂时不可用", 503)


class AdminApiClient:
    # 匿名函数: clock默认时钟在调用时读取当前UTC时间
    # 参数:
    #     无形参。
    # 返回: 带UTC时区的当前时间
    # 匿名函数: nonce_factory为每次内部签名生成新的随机数
    # 参数:
    #     无形参。
    # 返回: 由18字节随机数据编码出的URL安全字符串
    # 匿名函数: request_id_factory未配置时不附加上游请求标识
    # 参数:
    #     无形参。
    # 返回: None,表示当前没有可透传的请求标识
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        secret: bytes,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        nonce_factory: Callable[[], str] = lambda: secrets.token_urlsafe(18),
        request_id_factory: Callable[[], str | None] = lambda: None,
        traceparent_factory: Callable[[], str | None] = get_traceparent,
    ) -> None:
        # 功能:初始化带内部HMAC鉴权的管理端客户端并保存所需依赖与配置
        # 参数:
        #     self: 当前带内部HMAC鉴权的管理端客户端实例
        #     client: 异步HTTP客户端
        #     secret: 内部服务请求HMAC签名和验签的共享密钥
        #     clock: 提供当前时间的可替换时钟回调
        #     nonce_factory: 生成内部请求签名随机数的回调
        #     request_id_factory: 获取当前请求标识以向管理端透传的回调
        #     traceparent_factory: 获取当前链路上下文以向上游透传的回调
        # 返回:无返回值。
        self._client = client
        self._secret = secret
        self._clock = clock
        self._nonce_factory = nonce_factory
        self._request_id_factory = request_id_factory
        self._traceparent_factory = traceparent_factory
        self.timeout = httpx.Timeout(8.0, connect=2.0)

    async def get_modules(self) -> list[LearningModule]:
        # 功能:获取学习模块定义与目录版本信息
        # 参数:
        #     self: 当前带内部HMAC鉴权的管理端客户端实例
        # 返回:学习模块定义集合
        payload = await self._request_json("GET", "/internal/v1/learning/modules")
        return [_validate(LearningModule, item) for item in payload.get("items", [])]

    async def get_catalog(self, user_id: str, summary: Mapping[str, object]) -> LearningCatalog:
        # 功能:获取带用户摘要和授权信息的学习目录
        # 参数:
        #     self: 当前带内部HMAC鉴权的管理端客户端实例
        #     user_id: 当前操作所属用户的公开标识
        #     summary: 当前用户学习完成数等目录汇总字段
        # 返回:学习目录、用户摘要和权限信息
        del summary
        payload = await self._request_json(
            "POST", "/internal/v1/learning/catalog", {"user_id": user_id}
        )
        return LearningCatalog(items=payload.get("items", []))

    async def batch_access(self, user_id: str, scene_ids: Sequence[str]) -> list[AccessProjection]:
        # 功能:批量向管理端查询用户的场景访问权限
        # 参数:
        #     self: 当前带内部HMAC鉴权的管理端客户端实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_ids: 需要批量查询访问权限的场景公开标识序列
        # 返回:用户对场景的访问级别与授权期限集合
        payload = await self._request_json(
            "POST",
            "/internal/v1/access/batch",
            {"user_id": user_id, "scene_ids": list(scene_ids)},
        )
        return [_validate(AccessProjection, item) for item in payload.get("items", [])]

    async def open_scene(
        self, user_id: str, scene_id: str, idempotency_key: str
    ) -> SceneOpenResult:
        # 功能:向管理端申请场景访问并解析授权与发布内容契约
        # 参数:
        #     self: 当前带内部HMAC鉴权的管理端客户端实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        # 返回:访问级别与已发布场景内容
        payload = await self._request_json(
            "POST",
            f"/internal/v1/scenes/{scene_id}/open",
            {"user_id": user_id},
            idempotency_key=idempotency_key,
        )
        return _validate(SceneOpenResult, payload)

    async def get_entry(
        self,
        user_id: str,
        scene_id: str,
        entry_id: str,
        revision_id: str,
        entry_version: int,
        source_locator: str,
    ) -> SceneEntry:
        # 功能:按发布版本与来源定位查询权威词条内容
        # 参数:
        #     self: 当前带内部HMAC鉴权的管理端客户端实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     entry_id: 场景中的词条稳定标识
        #     revision_id: 需要访问或固定的场景发布修订标识
        #     entry_version: 固定词条的内容版本号
        #     source_locator: 词条来源在固定场景版本中的定位片段
        # 返回:固定发布版本的权威词条与来源快照
        payload = await self._request_json(
            "POST",
            f"/internal/v1/scenes/{scene_id}/entries/{entry_id}",
            {
                "user_id": user_id,
                "revision_id": revision_id,
                "entry_version": entry_version,
                "source_locator": source_locator,
            },
        )
        return _validate(SceneEntry, payload)

    async def get_signed_media(self, user_id: str, target_id: str) -> SignedMedia:
        # 功能:获取授权有效期内的媒体访问签名链接
        # 参数:
        #     self: 当前带内部HMAC鉴权的管理端客户端实例
        #     user_id: 当前操作所属用户的公开标识
        #     target_id: 需要学习或签发媒体链接的目标公开标识
        # 返回:授权有效期内的媒体签名链接
        payload = await self._request_json(
            "POST",
            f"/internal/v1/media/{target_id}/signed-url",
            {"user_id": user_id},
        )
        return _validate(SignedMedia, payload)

    async def get_signed_resource(
        self,
        user_id: str,
        scene_id: str,
        resource_id: str,
        revision_id: str,
    ) -> SignedResource:
        # 功能:按场景和发布版本获取资源签名链接
        # 参数:
        #     self: 当前带内部HMAC鉴权的管理端客户端实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     resource_id: 发布内容中的媒体资源标识
        #     revision_id: 需要访问或固定的场景发布修订标识
        # 返回:固定场景发布版本的资源签名链接
        payload = await self._request_json(
            "POST",
            f"/internal/v1/scenes/{scene_id}/resources/{resource_id}/signed-url",
            {"user_id": user_id, "revision_id": revision_id},
        )
        return _validate(SignedResource, payload)

    async def get_entitlements(self, user_id: str) -> EntitlementProjection:
        # 功能:获取用户当前授权权益投影
        # 参数:
        #     self: 当前带内部HMAC鉴权的管理端客户端实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:用户授权权益与到期时间投影
        payload = await self._request_json(
            "POST", "/internal/v1/entitlements", {"user_id": user_id}
        )
        return _validate(EntitlementProjection, payload)

    async def create_feedback(
        self,
        *,
        user_id: str,
        category: str,
        description: str,
        source: Mapping[str, object],
        screenshots: Sequence[str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        # 功能:提交当前用户反馈及来源和截图对象键
        # 参数:
        #     self: 当前带内部HMAC鉴权的管理端客户端实例
        #     user_id: 当前操作所属用户的公开标识
        #     category: 用户反馈的问题分类
        #     description: 用户提交的反馈问题说明
        #     source: 反馈或收藏产生的场景、词条与页面来源信息
        #     screenshots: 当前用户拥有的反馈截图OSS对象键列表
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        # 返回:新建反馈记录及其处理状态
        return await self._request_json(
            "POST",
            "/internal/v1/feedback",
            {
                "user_id": user_id,
                "category": category,
                "description": description,
                "source": dict(source),
                "screenshots": list(screenshots),
            },
            idempotency_key=idempotency_key,
            idempotency_header="X-Idempotency-Key",
        )

    async def list_feedback(self, user_id: str) -> list[dict[str, Any]]:
        # 功能:列出当前用户提交的反馈
        # 参数:
        #     self: 当前带内部HMAC鉴权的管理端客户端实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:当前用户反馈记录及处理状态列表
        payload = await self._request_json(
            "POST", "/internal/v1/feedback/query", {"user_id": user_id}
        )
        items = payload.get("items", [])
        return [dict(item) for item in items if isinstance(item, dict)]

    async def get_feedback(self, feedback_id: str) -> dict[str, Any]:
        # 功能:获取反馈记录及处理进度
        # 参数:
        #     self: 当前带内部HMAC鉴权的管理端客户端实例
        #     feedback_id: 用户反馈记录的公开标识
        # 返回:反馈标识、问题说明、截图对象键和处理进度
        return await self._request_json("GET", f"/internal/v1/feedback/{feedback_id}")

    async def supplement_feedback(
        self,
        feedback_id: str,
        user_id: str,
        text: str,
        idempotency_key: str,
        *,
        screenshots: list[str] | None = None,
    ) -> dict[str, Any]:
        # 功能:向管理端提交反馈补充文本与截图
        # 参数:
        #     self: 当前带内部HMAC鉴权的管理端客户端实例
        #     feedback_id: 用户反馈记录的公开标识
        #     user_id: 当前操作所属用户的公开标识
        #     text: 收藏英文词条原文,标准化后作为去重键
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        #     screenshots: 当前用户拥有的反馈截图OSS对象键列表
        # 返回:补充文本与截图后更新的反馈记录
        return await self._request_json(
            "POST",
            f"/internal/v1/feedback/{feedback_id}/supplements",
            {
                "user_id": user_id,
                "text": text,
                **({"screenshots": screenshots} if screenshots else {}),
            },
            idempotency_key=idempotency_key,
            idempotency_header="X-Idempotency-Key",
        )

    async def resolve_feedback(
        self,
        feedback_id: str,
        user_id: str,
        action: str,
        reason: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        # 功能:将反馈处理结果确认或异议发送至管理端
        # 参数:
        #     self: 当前带内部HMAC鉴权的管理端客户端实例
        #     feedback_id: 用户反馈记录的公开标识
        #     user_id: 当前操作所属用户的公开标识
        #     action: 用户对反馈处理结果的确认或异议动作
        #     reason: 撤销、纠错或反馈异议的业务原因说明
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        # 返回:用户确认或提出异议后更新的反馈记录
        return await self._request_json(
            "POST",
            f"/internal/v1/feedback/{feedback_id}/resolution",
            {"user_id": user_id, "action": action, "reason": reason},
            idempotency_key=idempotency_key,
            idempotency_header="X-Idempotency-Key",
        )

    async def delete_account_data(
        self, user_id: str, deletion_request_id: str, event_id: str
    ) -> dict[str, Any]:
        # 功能:通知管理端清理注销账号的跨域业务数据
        # 参数:
        #     self: 当前带内部HMAC鉴权的管理端客户端实例
        #     user_id: 当前操作所属用户的公开标识
        #     deletion_request_id: 账号注销申请的公开标识
        #     event_id: 业务事件或发件箱事件的去重标识
        # 返回:管理端接收账号数据清理命令的响应字段
        return await self._request_json(
            "POST",
            "/internal/v1/account-deletions",
            {
                "user_id": user_id,
                "deletion_request_id": deletion_request_id,
                "event_id": event_id,
            },
            idempotency_key=event_id,
            idempotency_header="X-Event-Id",
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        payload: Mapping[str, object] | None = None,
        *,
        idempotency_key: str | None = None,
        idempotency_header: str = "Idempotency-Key",
    ) -> dict[str, Any]:
        # 功能:发送带内部签名的JSON请求并按幂等条件重试
        # 参数:
        #     self: 当前带内部HMAC鉴权的管理端客户端实例
        #     method: 参与请求发送和签名的HTTP方法
        #     path: 待请求的内部接口路径
        #     payload: 小程序请求体中的结构化业务字段
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        #     idempotency_header: 传递幂等键的内部HTTP请求头名称
        # 返回:管理端HTTP响应解析得到的JSON对象
        body = (
            b""
            if payload is None
            else json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        retryable = method == "GET" or idempotency_key is not None
        attempts = 2 if retryable else 1
        for attempt in range(attempts):
            now = self._clock()
            timestamp = int(now.timestamp())
            nonce = self._nonce_factory()
            headers = {
                "Content-Type": "application/json",
                "X-Juya-Service": "juya-miniapp-api",
                "X-Juya-Timestamp": str(timestamp),
                "X-Juya-Nonce": nonce,
                "X-Juya-Signature": sign_request(
                    method, path, timestamp, nonce, body, self._secret
                ),
            }
            if idempotency_key is not None:
                headers[idempotency_header] = idempotency_key
            request_id = self._request_id_factory()
            if request_id:
                headers["X-Request-ID"] = request_id
            traceparent = self._traceparent_factory()
            if traceparent:
                headers["traceparent"] = traceparent
            try:
                started_at = time.perf_counter()
                response = await self._client.request(
                    method,
                    path,
                    content=body,
                    headers=headers,
                    timeout=self.timeout,
                )
            except httpx.TransportError as error:
                ADMIN_API_LATENCY.labels(operation=method, outcome="transport_error").observe(
                    time.perf_counter() - started_at
                )
                if attempt + 1 < attempts:
                    continue
                raise AdminApiUnavailable() from error
            ADMIN_API_LATENCY.labels(
                operation=method,
                outcome="success" if response.status_code < 400 else "upstream_error",
            ).observe(time.perf_counter() - started_at)
            if response.status_code >= 400:
                self._raise_upstream_error(response)
            try:
                value = response.json()
            except ValueError as error:
                raise AdminApiUnavailable() from error
            return value if isinstance(value, dict) else {"items": value}
        raise AdminApiUnavailable()

    @staticmethod
    def _raise_upstream_error(response: httpx.Response) -> None:
        # 功能:将管理端接口错误转换为安全的业务异常
        # 参数:
        #     response: 上游HTTP响应对象
        # 返回:无返回值。
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        code = payload.get("code") if isinstance(payload, dict) else None
        safe_code = code if isinstance(code, str) else "ADMIN_API_ERROR"
        if response.status_code >= 500:
            raise AdminApiUnavailable()
        message = payload.get("message") if isinstance(payload, dict) else None
        safe_message = message if isinstance(message, str) else "下游请求被拒绝"
        raise AppError(safe_code, safe_message, response.status_code)
