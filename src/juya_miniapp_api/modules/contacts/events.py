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
    # 功能:写入联系方式转化链路的匿名统计事件
    # 参数:
    #     session: 异步数据库会话
    #     event_key: 统计事件唯一键,重复写入时用于去重
    #     event_type: 统计或发件箱事件的业务类别
    #     user_id: 业务数据库中的用户内部数值主键
    #     occurred_at: 业务统计事件实际发生的时间
    #     payload: 统计事件的白名单业务字段,不包含敏感用户信息
    # 返回:是否新写入联系方式统计事件
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
