"""Public reading wrappers around the pure-reading and safe-preview domain DTOs."""

from juya_miniapp_api.integrations.admin_api.schemas import SignedResource
from juya_miniapp_api.modules.favorites.snapshots import StrictModel
from juya_miniapp_api.modules.short_reading.domain import ReadingPreview, ReadingProgress


class ReadingPageResponse(StrictModel):
    items: list[ReadingPreview]
    next_cursor: str | None
    has_more: bool


class ReadingCompleteResponse(StrictModel):
    progress: ReadingProgress
    created: bool


class ReadingResourceResponse(SignedResource):
    """Preserves the existing resource_id/url/expires_at signed resource shape."""
