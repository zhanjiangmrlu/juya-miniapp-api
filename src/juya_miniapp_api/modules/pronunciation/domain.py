from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PronunciationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    favorite_id: str | None = Field(default=None, min_length=1, max_length=64)
    content_type: Literal["dialogue", "word_card", "short_reading"] | None = None
    content_id: str | None = Field(default=None, min_length=1, max_length=64)
    content_version: int | None = Field(default=None, ge=1)
    entry_id: str | None = Field(default=None, min_length=1, max_length=64)
    sense_id: str | None = Field(default=None, min_length=1, max_length=64)
    source_id: str | None = Field(default=None, min_length=1, max_length=64)
    resource_version: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("content_version", mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("A boolean cannot identify a content version")
        return value

    @model_validator(mode="after")
    def has_authorization_reference(self) -> "PronunciationRequest":
        if self.favorite_id is None and (
            self.content_type is None
            or self.content_id is None
            or self.content_version is None
            or self.entry_id is None
        ):
            raise ValueError("Provide a favorite or a complete current content reference")
        return self


class PronunciationQuery(PronunciationRequest):
    target_id: str | None = Field(default=None, min_length=1, max_length=64)


class PronunciationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["READY", "PREPARING", "FAILED", "UNAVAILABLE"]
    url: str | None = None
    resource_version: str | None = None
    task_id: str | None = None
    can_retry: bool = False
    code: str | None = None
    expires_at: datetime | None = None
