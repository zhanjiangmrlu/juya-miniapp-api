from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header

from juya_miniapp_api.modules.learning.access_service import AccessService
from juya_miniapp_api.modules.learning.catalog_service import CatalogService
from juya_miniapp_api.modules.users.router import UserDependency


def create_content_access_router(
    catalog: CatalogService,
    access: AccessService,
    *,
    user_dependency: UserDependency,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["content-access"])

    @router.get("/learning/modules")
    async def modules() -> dict[str, object]:
        items = await catalog.modules()
        return {"items": [item.model_dump(mode="json") for item in items]}

    @router.get("/learning/catalog")
    async def learning_catalog(
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        result = await catalog.catalog(user_id, {})
        return result.model_dump(mode="json")

    @router.get("/learning/open-history")
    async def open_history(
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        return {"items": await access.open_history(user_id)}

    @router.post("/scenes/{scene_id}/open")
    async def open_scene(
        scene_id: str,
        user_id: Annotated[str, Depends(user_dependency)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> dict[str, object]:
        result = await access.open_scene(user_id, scene_id, idempotency_key, clock())
        return result.model_dump(mode="json")

    @router.get("/scenes/{scene_id}/entries/{entry_id}")
    async def scene_entry(
        scene_id: str,
        entry_id: str,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        result = await access.get_entry(user_id, scene_id, entry_id)
        return result.model_dump(mode="json")

    @router.post("/media/{target_id}/signed-url")
    async def signed_media(
        target_id: str,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        result = await access.get_signed_media(user_id, target_id, clock())
        return result.model_dump(mode="json")

    @router.get("/me/entitlements")
    async def entitlements(
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> dict[str, object]:
        result = await catalog.entitlements(user_id)
        return result.model_dump(mode="json")

    return router
