from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class InboxMessage:
    id: str
    user_id: str
    message_type: str
    title: str
    summary: str
    event_id: str
    created_at: datetime
    related_type: str | None = None
    related_id: str | None = None
    read_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MessagePage:
    items: list[InboxMessage]
    next_cursor: str | None
    has_more: bool
