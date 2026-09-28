from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Protocol

from juya_miniapp_api.infrastructure.redis.cache import JsonCache, bounded_cache_ttl
from juya_miniapp_api.integrations.admin_api.client import AdminApiUnavailable
from juya_miniapp_api.integrations.admin_api.schemas import (
    AccessProjection,
    EntitlementProjection,
    LearningCatalog,
    LearningModule,
)


class CatalogClient(Protocol):
    async def get_modules(self) -> list[LearningModule]: ...

    async def get_catalog(self, user_id: str, summary: Mapping[str, object]) -> LearningCatalog: ...

    async def batch_access(
        self, user_id: str, scene_ids: Sequence[str]
    ) -> list[AccessProjection]: ...

    async def get_entitlements(self, user_id: str) -> EntitlementProjection: ...


class CatalogService:
    def __init__(
        self,
        client: CatalogClient,
        cache: JsonCache | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._cache = cache
        self._clock = clock

    async def modules(self) -> list[LearningModule]:
        cached = await self._cache_get("learning-modules", "global", "v1")
        if cached is not None:
            raw_items = cached.get("items", [])
            items = raw_items if isinstance(raw_items, list) else []
            return [LearningModule.model_validate(item) for item in items if isinstance(item, dict)]
        try:
            modules = await self._client.get_modules()
        except AdminApiUnavailable:
            return []
        await self._cache_set(
            "learning-modules",
            "global",
            "v1",
            {"items": [item.model_dump(mode="json") for item in modules]},
            ttl_seconds=60,
        )
        return modules

    async def catalog(self, user_id: str, summary: Mapping[str, object]) -> LearningCatalog:
        try:
            entitlements = await self._client.get_entitlements(user_id)
            version = entitlements.version or "unversioned"
            cached = await self._cache_get("learning-catalog", user_id, version)
            if cached is not None:
                return LearningCatalog.model_validate(cached)
            catalog = await self._client.get_catalog(user_id, summary)
        except AdminApiUnavailable:
            return LearningCatalog(authorization_pending=True)
        if not catalog.authorization_pending:
            expirations = [
                expires_at
                for item in (*entitlements.formal, *entitlements.limited)
                if (expires_at := self._expiration(item.get("expires_at"))) is not None
            ]
            ttl = bounded_cache_ttl(
                self._clock(),
                30,
                min(expirations) if expirations else None,
            )
            await self._cache_set(
                "learning-catalog",
                user_id,
                version,
                catalog.model_dump(mode="json"),
                ttl_seconds=ttl,
            )
        return catalog

    async def access(self, user_id: str, scene_ids: Sequence[str]) -> list[AccessProjection]:
        try:
            return await self._client.batch_access(user_id, scene_ids)
        except AdminApiUnavailable:
            return []

    async def entitlements(self, user_id: str) -> EntitlementProjection:
        try:
            return await self._client.get_entitlements(user_id)
        except AdminApiUnavailable:
            return EntitlementProjection(authorization_pending=True)

    @staticmethod
    def _expiration(value: object) -> datetime | None:
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        return None

    async def _cache_get(
        self, namespace: str, identifier: str, version: str
    ) -> dict[str, object] | None:
        if self._cache is None:
            return None
        try:
            return await self._cache.get(namespace, identifier, version)
        except Exception:
            return None

    async def _cache_set(
        self,
        namespace: str,
        identifier: str,
        version: str,
        value: Mapping[str, object],
        *,
        ttl_seconds: int,
    ) -> None:
        if self._cache is None:
            return
        try:
            await self._cache.set(
                namespace,
                identifier,
                version,
                value,
                ttl_seconds=ttl_seconds,
            )
        except Exception:
            return
