"""Strict flat public favorite DTOs; stored legacy gaps remain explicit."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from juya_miniapp_api.integrations.admin_api.v15_content import SourceResolution
from juya_miniapp_api.modules.favorites.snapshots import (
    EntryType,
    PronunciationReference,
    StrictModel,
)

PublicSnapshotContentType = Literal["dialogue", "word_card", "SCENE"]


class FavoriteSnapshotResponse(StrictModel):
    content_type: PublicSnapshotContentType
    content_id: str = Field(min_length=1, max_length=64)
    content_version: int | None = Field(ge=1)
    entry_type: EntryType
    entry_id: str = Field(min_length=1, max_length=64)
    sense_id: str = Field(min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=64)
    schema_version: Literal[2]
    favorite_id: str = Field(min_length=1, max_length=64)
    active: bool
    snapshot_version: int = Field(ge=1)
    saved_content_version: int | None = Field(ge=1)
    revision_id: str | None = Field(min_length=1, max_length=64)
    english: str = Field(max_length=10000)
    phonetic: str = Field(max_length=200)
    meaning_zh: str = Field(max_length=10000)
    explanation: str = Field(max_length=10000)
    source_sentence: str = Field(max_length=10000)
    source_title: str = Field(max_length=200)
    # Historical locators may additionally carry a stored entry_id or integer anchor.
    source_locator: dict[str, str | int]
    image_asset_id: str | None
    pronunciation: PronunciationReference | None
    legacy_incomplete: bool
    missing_fields: list[str]
    favorited_at: datetime
    last_reviewed_at: datetime | None


class FavoriteSnapshotPageResponse(StrictModel):
    items: list[FavoriteSnapshotResponse]
    next_cursor: str | None
    has_more: bool


class FavoriteSnapshotCancelResponse(StrictModel):
    favorite_id: str = Field(min_length=1, max_length=64)
    active: bool
    snapshot_version: int = Field(ge=1)


class FavoriteSourceResolutionResponse(SourceResolution):
    content_type: PublicSnapshotContentType
    content_id: str = Field(min_length=1, max_length=64)
