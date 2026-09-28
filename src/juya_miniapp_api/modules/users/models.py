from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class UserSummary:
    public_id: str
    juya_number: str
    status: str


@dataclass(frozen=True, slots=True)
class UserProfile:
    public_id: str
    juya_number: str
    status: str
    nickname: str | None
    avatar_object_key: str | None
    created_at: datetime | None = None
    last_active_at: datetime | None = None
