from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from juya_miniapp_api.modules.favorites.snapshots import StrictModel


class Paragraph(StrictModel):
    paragraph_id: str = Field(min_length=1, max_length=64)
    english: str = Field(min_length=1, max_length=10000)


class WholeAudio(StrictModel):
    target_id: str
    resource_version: str
    duration_ms: int = Field(ge=1)


class ReadingPreview(StrictModel):
    content_id: str
    content_type: Literal["short_reading"] = "short_reading"
    title_en: str
    title_zh: str
    category: str
    topic: str
    level: str
    introduction: str
    cover_url: str | None = None
    access: Literal["TRIAL", "FORMAL", "LIMITED", "PREVIEW", "NONE"] = "PREVIEW"


class ReadingPosition(StrictModel):
    content_version: int = Field(ge=1)
    paragraph_id: str | None = Field(default=None, min_length=1, max_length=64)
    offset: int = Field(default=0, ge=0, le=10000)
    client_sequence: int = Field(default=0, ge=0, le=9223372036854775807)


class ReadingProgress(StrictModel):
    content_id: str
    saved_content_version: int = Field(ge=1)
    paragraph_id: str | None = None
    offset: int = 0
    client_sequence: int = Field(default=0, ge=0)
    started_at: datetime
    last_read_at: datetime
    completed_at: datetime | None = None


class ReadingCompletion(ReadingProgress):
    created: bool


class ReadingDetail(ReadingPreview):
    access: Literal["TRIAL", "FORMAL", "LIMITED"] = "TRIAL"
    content_version: int = Field(ge=1)
    revision_id: str
    schema_version: Literal[2] = 2
    paragraphs_en: list[Paragraph] = Field(min_length=1, max_length=1000)
    translation_zh: str = Field(min_length=1, max_length=1000000)
    whole_audio: WholeAudio | None = None
    progress: ReadingProgress | None = None

    @model_validator(mode="after")
    def unique_paragraphs(self) -> "ReadingDetail":
        ids = [row.paragraph_id for row in self.paragraphs_en]
        if len(ids) != len(set(ids)):
            raise ValueError("Paragraph identity must be unique")
        return self
