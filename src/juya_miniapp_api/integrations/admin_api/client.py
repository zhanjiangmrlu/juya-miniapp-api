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
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise AdminApiUnavailable() from error


class AdminApiUnavailable(AppError):
    def __init__(self) -> None:
        super().__init__("ADMIN_API_UNAVAILABLE", "内容与权益服务暂时不可用", 503)


class AdminApiClient:
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
        self._client = client
        self._secret = secret
        self._clock = clock
        self._nonce_factory = nonce_factory
        self._request_id_factory = request_id_factory
        self._traceparent_factory = traceparent_factory
        self.timeout = httpx.Timeout(8.0, connect=2.0)

    async def get_modules(self) -> list[LearningModule]:
        payload = await self._request_json("GET", "/internal/v1/learning/modules")
        return [_validate(LearningModule, item) for item in payload.get("items", [])]

    async def get_catalog(self, user_id: str, summary: Mapping[str, object]) -> LearningCatalog:
        del summary
        payload = await self._request_json(
            "POST", "/internal/v1/learning/catalog", {"user_id": user_id}
        )
        return LearningCatalog(items=payload.get("items", []))

    async def batch_access(self, user_id: str, scene_ids: Sequence[str]) -> list[AccessProjection]:
        payload = await self._request_json(
            "POST",
            "/internal/v1/access/batch",
            {"user_id": user_id, "scene_ids": list(scene_ids)},
        )
        return [_validate(AccessProjection, item) for item in payload.get("items", [])]

    async def open_scene(
        self, user_id: str, scene_id: str, idempotency_key: str
    ) -> SceneOpenResult:
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
        payload = await self._request_json(
            "POST",
            f"/internal/v1/scenes/{scene_id}/resources/{resource_id}/signed-url",
            {"user_id": user_id, "revision_id": revision_id},
        )
        return _validate(SignedResource, payload)

    async def get_entitlements(self, user_id: str) -> EntitlementProjection:
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
        payload = await self._request_json(
            "POST", "/internal/v1/feedback/query", {"user_id": user_id}
        )
        items = payload.get("items", [])
        return [dict(item) for item in items if isinstance(item, dict)]

    async def get_feedback(self, feedback_id: str) -> dict[str, Any]:
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
