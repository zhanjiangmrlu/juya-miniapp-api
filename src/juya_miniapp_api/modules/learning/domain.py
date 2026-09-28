from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class ReadingPosition:
    entry_id: str
    offset: int = 0


@dataclass(frozen=True, slots=True)
class LearningProgress:
    user_id: str
    scene_id: str
    source_type: str
    position: ReadingPosition
    client_sequence: int
    started_at: datetime
    completed_at: datetime | None
    last_learned_at: datetime


@dataclass(frozen=True, slots=True)
class CompletionResult:
    progress: LearningProgress
    created: bool
    checkin_date: date


@dataclass(frozen=True, slots=True)
class TodayTask:
    kind: str
    target_id: str
    card_ids: tuple[str, ...] = ()
