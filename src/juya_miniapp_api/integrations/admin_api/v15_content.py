"""Consumer DTOs for admin-api modules/content/v15_contracts.py.

Only favorite state is added by the BFF. All other fields are fixed to one
authoritatively published content version.
"""

from typing import Literal

from pydantic import Field, model_validator

from juya_miniapp_api.modules.favorites.snapshots import PronunciationReference, StrictModel


class ResourceReference(StrictModel):
    target_id: str
    resource_version: str
    duration_ms: int = Field(gt=0)


class SourceLocator(StrictModel):
    sentence_id: str | None = None
    paragraph_id: str | None = None


class ConfirmedEntry(StrictModel):
    entry_id: str = Field(min_length=1, max_length=64)
    entry_type: Literal["word", "chunk"]
    sense_id: str = Field(min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=64)
    entry_version: int = Field(default=1, ge=1)
    english: str = Field(min_length=1, max_length=10000)
    phonetic: str = Field(default="", max_length=200)
    meaning_zh: str = Field(min_length=1, max_length=10000)
    explanation: str = Field(default="", max_length=10000)
    source_sentence: str = Field(max_length=10000)
    source_locator: SourceLocator | None = None
    image_asset_id: str | None = None
    pronunciation: PronunciationReference | None = None
    legacy_incomplete: bool = False
    is_favorited: bool = False
    favorite_id: str | None = None

    @model_validator(mode="after")
    def validate_roles(self) -> "ConfirmedEntry":
        if self.entry_type == "chunk" and self.image_asset_id is not None:
            raise ValueError("Chunks cannot have display images")
        return self


class PublishedSentence(StrictModel):
    sentence_id: str
    speaker: str
    english: str = Field(max_length=10000)
    chinese: str = Field(max_length=10000)
    position: int = Field(ge=1)
    start_ms: int | None = None
    end_ms: int | None = None
    slice_target_id: str | None = None
    slice_version: str | None = None


class SceneDetail(StrictModel):
    content_id: str
    content_type: Literal["dialogue", "word_card"]
    content_version: int = Field(ge=1)
    revision_id: str
    schema_version: Literal[2] = 2
    title_en: str
    title_zh: str
    dialogue: list[PublishedSentence] = Field(default_factory=list)
    words: list[ConfirmedEntry] = Field(default_factory=list)
    chunks: list[ConfirmedEntry] = Field(default_factory=list)
    whole_audio: ResourceReference | None = None
    cover_asset_id: str | None = None
    learning_image_asset_id: str | None = None

    @model_validator(mode="after")
    def validate_collections(self) -> "SceneDetail":
        for kind, rows in (("word", self.words), ("chunk", self.chunks)):
            identities = [(row.entry_id, row.sense_id, row.source_id) for row in rows]
            if len(identities) != len(set(identities)) or any(
                row.entry_type != kind for row in rows
            ):
                raise ValueError("Entry type and stable identities must match their collection")
        ids = [row.sentence_id for row in self.dialogue]
        if len(ids) != len(set(ids)):
            raise ValueError("Sentence identities must be unique")
        return self


class ShortReadingDetail(StrictModel):
    content_id: str
    content_type: Literal["short_reading"] = "short_reading"
    content_version: int = Field(ge=1)
    revision_id: str
    schema_version: Literal[2] = 2
    title_en: str
    title_zh: str
    category: str
    topic: str
    level: str
    safe_intro: str
    paragraphs_en: list[str]
    paragraph_ids: list[str] = Field(default_factory=list)
    translation_zh: str
    whole_audio: ResourceReference | None = None
    cover_asset_id: str | None = None


class SourceResolution(StrictModel):
    saved_content_version: int | None
    target_content_version: int | None
    status: Literal["EXACT", "UPDATED_OR_UNRESOLVED", "NO_ACCESS"]
    locator: SourceLocator | None = None
    safe_summary: dict[str, str]

    @model_validator(mode="after")
    def locator_requires_exact_access(self) -> "SourceResolution":
        if self.status != "EXACT" and self.locator is not None:
            raise ValueError("Only exact authorized resolution may expose a locator")
        return self
