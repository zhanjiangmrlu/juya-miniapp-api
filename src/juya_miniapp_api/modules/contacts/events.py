"""Contact conversions and edits have distinct, anonymous event contracts."""

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from juya_miniapp_api.infrastructure.analytics_events import validate_event
from juya_miniapp_api.shared.ids import new_ulid


async def append_contact_event(
    session: AsyncSession,
    *,
    event_key: str,
    event_type: str,
    user_id: int,
    occurred_at: datetime,
    payload: Mapping[str, object] | None = None,
) -> bool:
    body = dict(payload or {})
    cohort = body.pop("contact_cohort", None)
    if cohort is not None and (
        not isinstance(cohort, str) or not re.fullmatch(r"[a-f0-9]{32}", cohort)
    ):
        raise ValueError("Contact cohort must be a random anonymous token")
    validate_event(
        "CONTACT_STATUS_CHANGED" if event_type == "CONTACT_CHANGED" else event_type, "ALL", body
    )
    if cohort is not None:
        body["contact_cohort"] = cohort
    if not event_key or len(event_key) > 191:
        raise ValueError("Invalid contact event key")
    result = await session.execute(
        text(
            "INSERT IGNORE INTO analytics_event "
            "(id,event_key,user_id,event_type,occurred_at,dimension,payload) VALUES "
            "(:id,:key,:user,:type,:at,'ALL',:payload)"
        ),
        {
            "id": new_ulid(occurred_at),
            "key": event_key,
            "user": user_id,
            "type": event_type,
            "at": occurred_at.astimezone(UTC).replace(tzinfo=None),
            "payload": json.dumps(body, separators=(",", ":")),
        },
    )
    return int(getattr(result, "rowcount", 0)) == 1
