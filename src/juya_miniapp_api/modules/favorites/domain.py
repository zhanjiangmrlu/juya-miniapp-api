from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class FavoriteSource:
    scene_id: str
    sentence_snapshot: str
    source_locator: str
    original_link: str | None = None
    revision_id: str | None = None
    entry_version: int = 1
    entry_snapshot: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FavoriteEntry:
    public_id: str
    user_id: str
    entry_type: str
    normalized_key: str
    entry_stable_id: str
    favorited_at: datetime
    last_reviewed_at: datetime | None
    sources: tuple[FavoriteSource, ...] = ()


@dataclass(slots=True)
class ReviewSession:
    id: str
    user_id: str
    review_type: str
    started_at: datetime
    completed_at: datetime | None
    card_count: int
    idempotency_key: str
    card_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReviewCompletion:
    session: ReviewSession
    created: bool
    checkin_date: date
