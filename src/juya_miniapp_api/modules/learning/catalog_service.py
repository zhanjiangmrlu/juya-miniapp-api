from collections.abc import Mapping, Sequence
from typing import Protocol

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
    def __init__(self, client: CatalogClient) -> None:
        self._client = client

    async def modules(self) -> list[LearningModule]:
        try:
            return await self._client.get_modules()
        except AdminApiUnavailable:
            return []

    async def catalog(self, user_id: str, summary: Mapping[str, object]) -> LearningCatalog:
        try:
            return await self._client.get_catalog(user_id, summary)
        except AdminApiUnavailable:
            return LearningCatalog(authorization_pending=True)

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
