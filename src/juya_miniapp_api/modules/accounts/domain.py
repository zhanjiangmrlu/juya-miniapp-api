from dataclasses import dataclass
from datetime import datetime
from typing import Literal

DeletionStatus = Literal["PENDING", "REVOKED", "DELETING", "DELETED", "FAILED"]
OutboxStatus = Literal["PENDING", "PROCESSING", "DELIVERED", "FAILED", "DEAD"]


@dataclass(slots=True)
class DeletionRequest:
    id: str
    user_id: str
    requested_at: datetime
    effective_at: datetime
    status: DeletionStatus
    revoked_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass(slots=True)
class OutboxEvent:
    id: str
    event_type: str
    aggregate_id: str
    payload: dict[str, object]
    status: OutboxStatus
    attempt_count: int
    next_attempt_at: datetime | None
    created_at: datetime
    processed_at: datetime | None = None
