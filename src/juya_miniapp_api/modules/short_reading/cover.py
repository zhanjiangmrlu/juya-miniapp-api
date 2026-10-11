"""Accept only the actual current-cover delegation contract; never construct media URLs."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, Field, field_validator

from juya_miniapp_api.integrations.admin_api.client import _validate
from juya_miniapp_api.modules.favorites.snapshots import StrictModel
from juya_miniapp_api.shared.errors import AppError


class PreviewCover(StrictModel):
    target_id: str = Field(strict=True, min_length=1, max_length=64)
    asset_id: str = Field(strict=True, min_length=1, max_length=64)
    role: Literal["COVER"]
    resource_version: None
    duration_ms: None
    url: str = Field(strict=True, min_length=1, max_length=8192)
    expires_at: AwareDatetime

    @field_validator("expires_at", mode="before")
    @classmethod
    def iso_expiry(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("Cover expiry must be an ISO datetime string")
        datetime.fromisoformat(value)
        return value


def current_cover_url(payload: object, asset_id: str, now: datetime) -> str:
    cover = _validate(PreviewCover, payload)
    try:
        address = urlsplit(cover.url)
        valid = (
            address.scheme == "https"
            and bool(address.hostname)
            and address.username is None
            and address.password is None
            and not address.fragment
            and address.port in {None, 443}
            and not any(character.isspace() or ord(character) < 32 for character in cover.url)
        )
    except ValueError:
        valid = False
    if not valid or cover.target_id != asset_id or cover.asset_id != asset_id:
        raise AppError("CONTENT_CONTRACT_INVALID", "短文封面契约不一致", 503)
    expiry = cover.expires_at.astimezone(UTC)
    # Actual SQLResourceSigner caps publication resources (including covers) at 60s.
    if not now < expiry <= now + timedelta(seconds=60):
        raise AppError("SIGNED_MEDIA_EXPIRED", "封面地址已失效", 503)
    return cover.url
