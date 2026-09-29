from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class ContactRecord:
    user_id: str
    wechat_id_ciphertext: bytes | None = field(repr=False)
    wechat_id_hmac: bytes | None = field(repr=False)
    consent_version: str | None
    consented_at: datetime | None
    source: str | None
    self_edit_count: int
    withdrawn_at: datetime | None
    change_pending: bool
    contact_status: str
    verified_at: datetime | None
    verified_by: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ContactView:
    user_id: str
    wechat_id: str | None = field(repr=False)
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


@dataclass(slots=True)
class CorrectionRequest:
    public_id: str
    user_id: str
    reason: str = field(repr=False)
    status: str = "PENDING"
    created_at: datetime | None = None
    processed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ContactTimelineEvent:
    status: str
    actor_type: str
    actor_id: str
    event_type: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class AdminCorrectionRecord:
    id: str
    user_id: str
    juya_number: str
    nickname: str | None
    wechat_id_ciphertext: bytes | None = field(repr=False)
    reason: str = field(repr=False)
    status: str = "PENDING"
    created_at: datetime | None = None
    processed_at: datetime | None = None
    timeline: tuple[ContactTimelineEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class AdminCorrectionView:
    id: str
    user_id: str
    juya_number: str
    nickname: str | None
    wechat_id: str | None = field(repr=False)
    reason: str = field(repr=False)
    status: str = "PENDING"
    created_at: datetime | None = None
    processed_at: datetime | None = None
    timeline: tuple[ContactTimelineEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class ContactAuditEvent:
    user_id: str
    event_type: str
    actor_type: str
    actor_id: str
    occurred_at: datetime
