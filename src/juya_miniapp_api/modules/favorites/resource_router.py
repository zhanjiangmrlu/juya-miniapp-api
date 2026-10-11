from typing import Annotated

from fastapi import APIRouter, Depends, Response

from juya_miniapp_api.modules.favorites.resources import (
    FavoriteResourceResponse,
    FavoriteResourceService,
)
from juya_miniapp_api.modules.users.router import UserDependency


def create_favorite_resource_router(
    service: FavoriteResourceService, *, user_dependency: UserDependency
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/favorite-snapshots", tags=["favorite-snapshots"])

    @router.get(
        "/{favorite_id}/resources/{target_id}/signed-url", response_model=FavoriteResourceResponse
    )
    async def signed_url(
        favorite_id: str,
        target_id: str,
        response: Response,
        user_id: Annotated[str, Depends(user_dependency)],
    ) -> FavoriteResourceResponse:
        response.headers["Cache-Control"] = "private, no-store"
        return await service.signed_url(user_id, favorite_id, target_id)

    return router
