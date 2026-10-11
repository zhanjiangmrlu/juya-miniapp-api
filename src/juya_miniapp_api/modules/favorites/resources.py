from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

from juya_miniapp_api.modules.favorites.snapshots import (
    FavoriteSnapshot,
    SnapshotService,
    StrictModel,
)
from juya_miniapp_api.shared.errors import AppError


class FavoriteResourceClient(Protocol):
    async def review_resource(
        self, user_id: str, favorite_id: str, target_id: str, resource_version: str | None
    ) -> dict[str, Any]: ...


class FavoriteResourceResponse(StrictModel):
    resource_id: str
    url: str
    expires_at: datetime


class FavoriteResourceService:
    def __init__(
        self,
        snapshots: SnapshotService,
        client: FavoriteResourceClient,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.snapshots = snapshots
        self.client = client
        self.clock = clock

    async def signed_url(
        self, user_id: str, favorite_id: str, target_id: str
    ) -> FavoriteResourceResponse:
        before = await self.snapshots.detail(user_id, favorite_id)
        if before.data.reference.entry_type != "word" or before.data.image_asset_id != target_id:
            raise AppError("FAVORITE_RESOURCE_ACCESS_DENIED", "收藏没有对应固定词汇图片", 403)
        payload = await self.client.review_resource(user_id, favorite_id, target_id, None)
        # An upstream read cannot authorize a cancelled or newly collected local snapshot.
        after = await self.snapshots.detail(user_id, favorite_id)
        if self._binding(before) != self._binding(after):
            raise AppError("FAVORITE_VERSION_CONFLICT", "收藏已变化,请刷新后重试", 409)
        try:
            if (
                payload.get("target_id") != target_id
                or payload.get("asset_id", target_id) != target_id
                or payload.get("resource_version") is not None
                or payload.get("role", "WORD_IMAGE") != "WORD_IMAGE"
            ):
                raise ValueError("Resource reference mismatch")
            url = payload.get("url")
            if not isinstance(url, str):
                raise ValueError("Missing signed URL")
            parsed = urlsplit(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValueError("Invalid signed URL")
            _ = parsed.port
            expiry = payload.get("expires_at")
            if isinstance(expiry, str):
                expiry = datetime.fromisoformat(expiry)
            if not isinstance(expiry, datetime) or expiry.tzinfo is None or expiry <= self.clock():
                raise ValueError("Expired or invalid signed URL")
        except (ValueError, TypeError, AttributeError, OverflowError):
            raise AppError(
                "FAVORITE_RESOURCE_CONTRACT_INVALID", "收藏图片地址暂不可用", 503
            ) from None
        return FavoriteResourceResponse(
            resource_id=target_id, url=url, expires_at=expiry.astimezone(UTC)
        )

    @staticmethod
    def _binding(row: FavoriteSnapshot) -> tuple[object, ...]:
        return (
            row.favorite_id,
            row.snapshot_version,
            row.snapshot_revision_id,
            row.saved_content_version,
            row.data.revision_id,
            row.data.reference,
            row.data.image_asset_id,
        )
