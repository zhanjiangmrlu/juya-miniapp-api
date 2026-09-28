from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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


class SceneOpenResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access: str | None = None
    sources: list[str] = Field(default_factory=list)
    earliest_expires_at: datetime | None = None
    activated_at: datetime | None = None
    scene: dict[str, Any] | None = None
    authorization_pending: bool = False


class SceneEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    public_id: str | None = None
    scene_id: str | None = None


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
