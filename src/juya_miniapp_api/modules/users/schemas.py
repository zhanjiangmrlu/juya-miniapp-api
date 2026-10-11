from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProfileContactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wechat_id: str | None
    self_edit_count: int
    change_pending: bool
    contact_status: str
    can_self_edit: bool
    requires_correction: bool
    consent_version: str | None
    consented_at: datetime | None
    withdrawn_at: datetime | None
    verified_at: datetime | None
    verified_by: str | None
    updated_at: datetime


class MeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_id: str
    juya_number: str
    status: str
    nickname: str | None
    avatar_object_key: str | None
    avatar_url: str | None
    created_at: datetime | None
    last_active_at: datetime | None
    contact: ProfileContactResponse | None
    contact_prompt_eligible: bool
    deletion: dict[str, object] | None


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
