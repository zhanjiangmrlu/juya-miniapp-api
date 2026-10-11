"""Presentation stage from owned, current entitlements and recorded learning history.

This projection never grants content access. Contact/profile completion is not a
stage condition (V1.5 section 6.1).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from juya_miniapp_api.shared.errors import AppError

LearningStage = Literal["NEW", "ENTITLED"]


@dataclass(frozen=True)
class LearningStageFacts:
    has_valid_entitlement: bool
    has_started_non_open: bool

    @property
    def stage(self) -> LearningStage:
        return "ENTITLED" if self.has_valid_entitlement or self.has_started_non_open else "NEW"


class LearningStageReader(Protocol):
    async def get(self, user_id: str, now: datetime) -> LearningStageFacts: ...


class SQLLearningStageReader:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def get(self, user_id: str, now: datetime) -> LearningStageFacts:
        # One statement obtains a consistent owned view. Publication/module access
        # remains with admin; hiding all catalog rows must not reset the user stage.
        async with self.sessions() as session:
            row = (
                (
                    await session.execute(
                        text("""
                SELECT u.status,
                  (EXISTS(SELECT 1 FROM formal_entitlement e WHERE e.user_id=u.id
                      AND e.status='ACTIVE' AND (e.expires_at IS NULL OR e.expires_at>:now))
                   OR EXISTS(SELECT 1 FROM limited_entitlement e WHERE e.user_id=u.id
                      AND ((e.status='PENDING' AND e.start_deadline>:now)
                       OR (e.status='ACTIVE' AND e.expires_at>:now)))) AS entitled,
                  (EXISTS(SELECT 1 FROM learning_progress p WHERE p.user_id=u.id
                      AND p.source_type='SCENE' AND p.started_at IS NOT NULL
                      AND NOT EXISTS(SELECT 1 FROM open_scene_item i
                        JOIN scene s ON s.id=i.scene_id
                        WHERE s.public_id=p.scene_id AND i.config_id=(
                          SELECT c.id FROM open_scene_config c
                          WHERE c.activated_at<=p.started_at
                          ORDER BY c.version DESC,c.id DESC LIMIT 1)))
                   OR EXISTS(SELECT 1 FROM short_reading_progress p WHERE p.user_id=u.id
                      AND p.started_at IS NOT NULL)) AS started
                FROM user_account u WHERE u.public_id=:user
            """),
                        {"user": user_id, "now": now.astimezone(UTC).replace(tzinfo=None)},
                    )
                )
                .mappings()
                .first()
            )
        if row is None or row["status"] != "ACTIVE":
            raise AppError("ACCOUNT_NOT_ACTIVE", "账号当前不可用", 403)
        return LearningStageFacts(bool(row["entitled"]), bool(row["started"]))
