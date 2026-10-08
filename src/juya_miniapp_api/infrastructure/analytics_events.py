"""Immutable business events; never accept identifying strings in payloads."""

import json
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from juya_miniapp_api.shared.ids import new_ulid

_ANONYMOUS_ENUMS = {
    "ALL",
    "NOT_PROVIDED",
    "PENDING",
    "CONTACTED",
    "UNREACHABLE",
    "DO_NOT_CONTACT",
    "ACTIVE",
    "EXPIRED",
    "ENDED",
    "START_EXPIRED",
    "PAUSED",
    "PRONUNCIATION",
    "DISPLAY",
    "FUNCTION",
    "REVOKED",
    "CANCELLED",
    "RESOLVED",
    "PROCESSING",
    "NEED_MORE",
    "USER_SUPPLIED",
    "CLOSED_INSUFFICIENT",
    "CONTENT",
    "TECHNICAL",
    "OTHER",
    "MODE_3",
    "MODE_5",
}


def is_anonymous_dimension(value: str) -> bool:
    # 功能:校验统计维度不含用户可识别信息
    # 参数:
    #     value: 需要检查是否包含用户身份信息的统计维度
    # 返回:统计维度是否满足匿名字段约束
    return value in _ANONYMOUS_ENUMS or (
        bool(re.fullmatch(r"(?:scene|series|package|campaign):[A-Za-z0-9_-]{1,64}", value))
        and not re.search(
            r"user|wechat|openid|phone|mobile|email|nickname|juya|screenshot|trajectory|wx[_-]?id",
            value,
            re.I,
        )
    )


EVENT_METRICS = {
    "USER_CREATED": "NEW_USERS",
    "USER_ACTIVE": "ACTIVE_USERS",
    "USER_ACTIVE_WEEK": "WEEK_ACTIVE_USERS",
    "USER_ACTIVE_MONTH": "MONTH_ACTIVE_USERS",
    "SCENE_STARTED": "SCENE_STARTS",
    "SCENE_COMPLETED": "SCENE_COMPLETIONS",
    "OPEN_SCENE_COMPLETED": "OPEN_SCENE_COMPLETIONS",
    "OPEN_LEARNER_STARTED": "OPEN_LEARNERS",
    "OPEN_ALL_COMPLETED": "OPEN_ALL_COMPLETIONS",
    "CONTACT_PROMPT_EXPOSED": "CONTACT_EXPOSURES",
    "CONTACT_SUBMITTED": "CONTACT_SUBMISSIONS",
    "CONTACT_CHANGED": "CONTACT_CHANGES",
    "CONTACT_WITHDRAWN": "CONTACT_WITHDRAWALS",
    "CONTACT_STATUS_CHANGED": "CONTACT_STATES",
    "FORMAL_GRANTED": "FORMAL_ENTITLEMENTS",
    "FORMAL_STATUS_CHANGED": "FORMAL_STATES",
    "LIMITED_GRANTED": "LIMITED_GRANTS",
    "LIMITED_STARTED": "LIMITED_STARTS",
    "LIMITED_EXPIRED": "LIMITED_EXPIRATIONS",
    "LIMITED_START_EXPIRED": "LIMITED_START_EXPIRATIONS",
    "LIMITED_COMPLETED": "LIMITED_COMPLETIONS",
    "LIMITED_STATUS_CHANGED": "LIMITED_STATES",
    "FAVORITE_CREATED": "FAVORITES",
    "REVIEW_COMPLETED": "REVIEWS",
    "SCENE_REVISITED": "REVISITS",
    "FEEDBACK_CREATED": "FEEDBACK_NEW",
    "FEEDBACK_RESPONDED": "FEEDBACK_RESPONSES",
    "FEEDBACK_SUPPLEMENTED": "FEEDBACK_SUPPLEMENTS",
    "FEEDBACK_RESOLVED": "FEEDBACK_RESOLUTIONS",
    "FEEDBACK_REOPENED": "FEEDBACK_REOPENS",
    "FEEDBACK_OVERDUE": "FEEDBACK_TIMEOUTS",
    "FEEDBACK_STATUS_CHANGED": "FEEDBACK_STATES",
    "DELETION_REQUESTED": "DELETION_REQUESTS",
    "DELETION_WITHDRAWN": "DELETION_WITHDRAWALS",
    "DELETION_EFFECTIVE": "DELETIONS",
}
PAYLOAD_FIELDS = frozenset(
    {
        "mode",
        "status",
        "category",
        "response_seconds",
        "supplement_rounds",
        "before_expiry",
        "prompted",
        "cohort_day",
        "started_day",
        "created_day",
        "contact_cohort",
    }
)


def validate_event(event_type: str, dimension: str, payload: Mapping[str, object]) -> None:
    # 功能:校验统计事件类型、匿名维度与白名单载荷
    # 参数:
    #     event_type: 统计或发件箱事件的业务类别
    #     dimension: 业务统计使用的匿名维度标签
    #     payload: 统计事件的白名单业务字段,不包含敏感用户信息
    # 返回:无返回值。
    if event_type not in EVENT_METRICS or not is_anonymous_dimension(dimension):
        raise ValueError("Unsupported analytics event or non-anonymous dimension")
    if set(payload) - PAYLOAD_FIELDS:
        raise ValueError("Analytics payload contains unsupported personal fields")
    for key, value in payload.items():
        if key == "contact_cohort":
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
                raise ValueError("Analytics contact cohort must be anonymous random hex")
        elif key in {"cohort_day", "started_day", "created_day"}:
            if not isinstance(value, str):
                raise ValueError("Analytics cohort must be an ISO day")
            date.fromisoformat(value)
        elif key in {"status", "category"}:
            if not isinstance(value, str) or not is_anonymous_dimension(value):
                raise ValueError("Analytics payload enum is invalid")
        elif key == "mode":
            if value not in {3, 5}:
                raise ValueError("Analytics mode must be 3 or 5")
        elif key in {"before_expiry", "prompted"}:
            if not isinstance(value, bool):
                raise ValueError("before_expiry must be a boolean")
        elif not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("Analytics numeric payload is invalid")


async def append_event(
    session: AsyncSession,
    *,
    event_key: str,
    event_type: str,
    user_id: int | None,
    occurred_at: datetime,
    dimension: str = "ALL",
    payload: Mapping[str, object] | None = None,
) -> bool:
    # 功能:在当前事务中写入去重的业务统计事件
    # 参数:
    #     session: 异步数据库会话
    #     event_key: 统计事件唯一键,重复写入时用于去重
    #     event_type: 统计或发件箱事件的业务类别
    #     user_id: 业务数据库中的用户内部数值主键; 匿名事件可为None
    #     occurred_at: 业务统计事件实际发生的时间
    #     dimension: 业务统计使用的匿名维度标签
    #     payload: 统计事件的白名单业务字段,不包含敏感用户信息
    # 返回:是否新写入事件;重复事件返回False
    body = dict(payload or {})
    validate_event(event_type, dimension, body)
    if not event_key or len(event_key) > 191:
        raise ValueError("Invalid event key")
    result = await session.execute(
        text(
            "INSERT IGNORE INTO analytics_event "
            "(id,event_key,user_id,event_type,occurred_at,dimension,payload) "
            "VALUES (:id,:key,:user,:type,:at,:dimension,:payload)"
        ),
        {
            "id": new_ulid(occurred_at),
            "key": event_key,
            "user": user_id,
            "type": event_type,
            "at": occurred_at.astimezone(UTC).replace(tzinfo=None),
            "dimension": dimension,
            "payload": json.dumps(body, separators=(",", ":")),
        },
    )
    return int(getattr(result, "rowcount", 0)) == 1


async def append_activity_events(
    session: AsyncSession, user_id: int, occurred_at: datetime
) -> None:
    # 功能:写入去重的用户活跃统计事件
    # 参数:
    #     session: 异步数据库会话
    #     user_id: 业务数据库中的用户内部数值主键
    #     occurred_at: 业务统计事件实际发生的时间
    # 返回:无返回值。
    """First successful learning operation per Beijing day / calendar week / month."""
    day = occurred_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
    for event_type, prefix, period_day in (
        ("USER_ACTIVE", "active", day),
        ("USER_ACTIVE_WEEK", "active-week", day - timedelta(days=day.weekday())),
        ("USER_ACTIVE_MONTH", "active-month", day.replace(day=1)),
    ):
        await append_event(
            session,
            event_key=f"{prefix}:{user_id}:{period_day.isoformat()}",
            event_type=event_type,
            user_id=user_id,
            occurred_at=occurred_at,
        )
