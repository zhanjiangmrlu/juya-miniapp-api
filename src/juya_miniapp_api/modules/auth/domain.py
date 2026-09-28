from dataclasses import dataclass, field
from datetime import datetime

from juya_miniapp_api.modules.users.models import UserSummary


@dataclass(slots=True)
class SessionRecord:
    id: str
    user: UserSummary
    refresh_token_hash: bytes = field(repr=False)
    expires_at: datetime
    device_digest: bytes
    revoked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SessionTokens:
    session_id: str
    user: UserSummary
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    refresh_expires_at: datetime
    account_summary: dict[str, object]
