from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nickname: str | None = Field(default=None, max_length=64)
    avatar_object_key: str | None = Field(default=None, max_length=512)


class AvatarUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_type: str = Field(min_length=1, max_length=64)


class AvatarUploadCredentialResponse(BaseModel):
    host: str
    key: str
    fields: dict[str, str]
    content_type: str
    max_bytes: int
    expires_at: datetime
    policy: str
    signature: str
    access_key_id: str


class ContactSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wechat_id: str = Field(min_length=1, max_length=64)
    consent_version: str = Field(min_length=1, max_length=32)
    consent_confirmed: bool
    source: str = Field(default="PROFILE", min_length=1, max_length=32)


class CorrectionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=2, max_length=500)
