from datetime import UTC, datetime, timedelta

import pytest

from juya_miniapp_api.infrastructure.redis.cache import JsonCache
from juya_miniapp_api.integrations.admin_api.schemas import (
    AccessProjection,
    EntitlementProjection,
    LearningCatalog,
    LearningModule,
)
from juya_miniapp_api.modules.learning.catalog_service import CatalogService

NOW = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, name: str) -> str | None:
        return self.values.get(name)

    async def set(self, name: str, value: str, *, ex: int) -> None:
        self.values[name] = value
        self.ttls[name] = ex

    async def delete(self, *names: str) -> None:
        for name in names:
            self.values.pop(name, None)


class FakeCatalogClient:
    def __init__(self) -> None:
        self.module_calls = 0
        self.catalog_calls = 0

    async def get_modules(self) -> list[LearningModule]:
        self.module_calls += 1
        return [LearningModule(key="scene_learning", title="场景学习")]

    async def get_catalog(self, user_id: str, summary: object) -> LearningCatalog:
        del user_id, summary
        self.catalog_calls += 1
        return LearningCatalog(items=[{"scene_id": "scene-1"}])

    async def batch_access(self, user_id: str, scene_ids: object) -> list[AccessProjection]:
        del user_id, scene_ids
        return []

    async def get_entitlements(self, user_id: str) -> EntitlementProjection:
        del user_id
        return EntitlementProjection(
            version="v2",
            limited=[{"expires_at": NOW + timedelta(seconds=8)}],
        )


@pytest.mark.asyncio
async def test_catalog_cache_uses_version_and_never_exceeds_entitlement() -> None:
    redis = FakeRedis()
    client = FakeCatalogClient()
    service = CatalogService(client, JsonCache(redis), clock=lambda: NOW)

    assert len(await service.modules()) == 1
    assert len(await service.modules()) == 1
    first = await service.catalog("user-1", {})
    second = await service.catalog("user-1", {})

    assert client.module_calls == 1
    assert client.catalog_calls == 1
    assert first == second
    assert redis.ttls["juya:cache:learning-catalog:v2:user-1"] == 8
