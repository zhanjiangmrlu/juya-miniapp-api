from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from juya_miniapp_api.integrations.admin_api.content import SceneContent
from juya_miniapp_api.integrations.admin_api.content import SceneEntry as ContentEntry


class LearningModule(BaseModel):
    model_config = ConfigDict(extra="allow")

    public_id: str | None = None
    key: str | None = None
    title: str | None = None
    enabled: bool = True


class LearningCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, Any]] = Field(default_factory=list)
    authorization_pending: bool = False


class AccessProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str
    level: str
    sources: list[str] = Field(default_factory=list)
    earliest_expires_at: datetime | None = None


class PublishedScene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str
    revision_id: str
    content_version: int = Field(ge=1)
    content: SceneContent


class PreviewScene(BaseModel):
    model_config = ConfigDict(extra="forbid")
    public_id: str
    title: str
    title_en: str = ""
    title_zh: str = ""
    series: str | None = None
    cover_url: str | None = None
    introduction: str | None = None
    preview_status: Literal["PREVIEW"] = "PREVIEW"


class SceneOpenResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access: Literal["OPEN", "FORMAL", "LIMITED", "PREVIEW"] | None = None
    sources: list[str] = Field(default_factory=list)
    earliest_expires_at: datetime | None = None
    activated_at: datetime | None = None
    scene: PublishedScene | PreviewScene | None = None
    authorization_pending: bool = False

    @model_validator(mode="after")
    def validate_access_shape(self) -> "SceneOpenResult":
        if self.scene is not None:
            if self.access == "PREVIEW" and not isinstance(self.scene, PreviewScene):
                raise ValueError("Preview access cannot contain published content")
            if self.access != "PREVIEW" and not isinstance(self.scene, PublishedScene):
                raise ValueError("Full access requires published content")
        return self


class SceneEntry(ContentEntry):
    scene_id: str
    revision_id: str
    source_locator: str
    entry_type: str | None = None
    sentence_snapshot: str = ""


class SignedResource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_id: str
    url: str
    expires_at: datetime


class SignedMedia(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str
    url: str
    expires_at: datetime


class EntitlementProjection(BaseModel):
    model_config = ConfigDict(extra="allow")

    formal: list[dict[str, Any]] = Field(default_factory=list)
    limited: list[dict[str, Any]] = Field(default_factory=list)
    version: str | None = None
    authorization_pending: bool = False
