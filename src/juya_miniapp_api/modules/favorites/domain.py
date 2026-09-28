from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class FavoriteSource:
    scene_id: str
    sentence_snapshot: str
    source_locator: str
    original_link: str | None = None


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


@dataclass(frozen=True, slots=True)
class ReviewCompletion:
    session: ReviewSession
    created: bool
    checkin_date: date
