import asyncio
import json
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from juya_miniapp_api.infrastructure.analytics_events import append_activity_events, append_event
from juya_miniapp_api.modules.checkins.service import beijing_learning_date
from juya_miniapp_api.modules.learning.domain import (
    CompletionResult,
    LearningProgress,
    ReadingPosition,
)
from juya_miniapp_api.shared.errors import AppError


class LearningRepository(Protocol):
    async def save_progress(
        self,
        user_id: str,
        scene_id: str,
        client_sequence: int,
        position: ReadingPosition,
        now: datetime,
    ) -> LearningProgress:
        # 功能:按客户端序号保存场景阅读位置并拒绝旧请求覆盖
        # 参数:
        #     self: 当前场景阅读进度与完成事件仓库实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     client_sequence: 客户端递增的进度请求序号,防止旧位置覆盖新位置
        #     position: 当前场景词条标识与阅读偏移位置
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:场景阅读位置、请求序号与学习时间
        ...

    async def complete(
        self,
        user_id: str,
        scene_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> CompletionResult:
        # 功能:幂等完成场景学习并记录完成事件与北京时间打卡
        # 参数:
        #     self: 当前场景阅读进度与完成事件仓库实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:学习进度、首次完成标记与打卡日期
        ...


class InMemoryLearningRepository:
    def __init__(self) -> None:
        # 功能:初始化场景学习的InMemoryLearningRepository对象的状态存储
        # 参数:
        #     self: 当前场景学习的InMemoryLearningRepository实例
        # 返回:无返回值。
        self.progress: dict[tuple[str, str], LearningProgress] = {}
        self.completion_events: dict[tuple[str, str], str] = {}
        self.idempotency_keys: dict[tuple[str, str], tuple[str, str]] = {}
        self.checkins: set[tuple[str, object]] = set()
        self._lock = asyncio.Lock()

    async def save_progress(
        self,
        user_id: str,
        scene_id: str,
        client_sequence: int,
        position: ReadingPosition,
        now: datetime,
    ) -> LearningProgress:
        # 功能:按客户端序号保存场景阅读位置并拒绝旧请求覆盖
        # 参数:
        #     self: 当前场景学习的InMemoryLearningRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     client_sequence: 客户端递增的进度请求序号,防止旧位置覆盖新位置
        #     position: 当前场景词条标识与阅读偏移位置
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:场景阅读位置、请求序号与学习时间
        async with self._lock:
            key = (user_id, scene_id)
            current = self.progress.get(key)
            if current is not None and client_sequence <= current.client_sequence:
                return current
            updated = LearningProgress(
                user_id=user_id,
                scene_id=scene_id,
                source_type="SCENE",
                position=position,
                client_sequence=client_sequence,
                started_at=current.started_at if current else now,
                completed_at=current.completed_at if current else None,
                last_learned_at=now,
            )
            self.progress[key] = updated
            return updated

    async def complete(
        self,
        user_id: str,
        scene_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> CompletionResult:
        # 功能:幂等完成场景学习并记录完成事件与北京时间打卡
        # 参数:
        #     self: 当前场景学习的InMemoryLearningRepository实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:学习进度、首次完成标记与打卡日期
        async with self._lock:
            key = (user_id, scene_id)
            current = self.progress.get(key)
            created = current is None or current.completed_at is None
            if current is None:
                current = LearningProgress(
                    user_id,
                    scene_id,
                    "SCENE",
                    ReadingPosition("start", 0),
                    0,
                    now,
                    now,
                    now,
                )
            elif created:
                current = LearningProgress(
                    current.user_id,
                    current.scene_id,
                    current.source_type,
                    current.position,
                    current.client_sequence,
                    current.started_at,
                    now,
                    now,
                )
            self.progress[key] = current
            if created:
                self.completion_events[key] = idempotency_key
                self.idempotency_keys[(user_id, idempotency_key)] = key
            learning_day = beijing_learning_date(now)
            self.checkins.add((user_id, learning_day))
            return CompletionResult(current, created, learning_day)


def _database_datetime(value: datetime) -> datetime:
    # 功能:将时间转换为数据库保存的无时区UTC时间
    # 参数:
    #     value: 待转换时区的必填数据库或业务时间
    # 返回:转换后的无时区UTC时间
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _utc_datetime(value: datetime | None) -> datetime | None:
    # 功能:将数据库时间统一为带UTC时区的时间并保留空值
    # 参数:
    #     value: 待转换时区的数据库或业务时间;空值保留为空
    # 返回:带UTC时区的时间;原值为空时返回None
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SQLAlchemyLearningRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        # 功能:初始化SQL场景学习进度仓库并保存所需依赖与配置
        # 参数:
        #     self: 当前SQL场景学习进度仓库实例
        #     session_factory: 创建数据库事务会话的异步工厂
        # 返回:无返回值。
        self._session_factory = session_factory

    async def save_progress(
        self,
        user_id: str,
        scene_id: str,
        client_sequence: int,
        position: ReadingPosition,
        now: datetime,
        *,
        is_scene_open: bool = False,
    ) -> LearningProgress:
        # 功能:按客户端序号保存场景阅读位置并拒绝旧请求覆盖
        # 参数:
        #     self: 当前SQL场景学习进度仓库实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     client_sequence: 客户端递增的进度请求序号,防止旧位置覆盖新位置
        #     position: 当前场景词条标识与阅读偏移位置
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        #     is_scene_open: 场景是否属于当前开放内容配置
        # 返回:场景阅读位置、请求序号与学习时间
        async with self._session_factory() as session, session.begin():
            internal_id = await self._lock_user(session, user_id)
            current = await self._load_progress(session, user_id, scene_id, for_update=True)
            serialized_position = json.dumps(
                {"entry_id": position.entry_id, "offset": position.offset},
                separators=(",", ":"),
            )
            if current is None:
                await session.execute(
                    text(
                        "INSERT INTO learning_progress "
                        "(user_id, scene_id, source_type, position, last_client_sequence, "
                        "started_at, last_learned_at) VALUES "
                        "(:user_id, :scene_id, 'SCENE', :position, :sequence, :now, :now)"
                    ),
                    {
                        "user_id": internal_id,
                        "scene_id": scene_id,
                        "position": serialized_position,
                        "sequence": client_sequence,
                        "now": _database_datetime(now),
                    },
                )
            elif client_sequence > current.client_sequence:
                await session.execute(
                    text(
                        "UPDATE learning_progress SET position = :position, "
                        "last_client_sequence = :sequence, last_learned_at = :now "
                        "WHERE user_id = :user_id AND scene_id = :scene_id"
                    ),
                    {
                        "position": serialized_position,
                        "sequence": client_sequence,
                        "now": _database_datetime(now),
                        "user_id": internal_id,
                        "scene_id": scene_id,
                    },
                )
            day = beijing_learning_date(now).isoformat()
            await append_activity_events(session, internal_id, now)
            if current is None:
                await self._record_open_start(session, internal_id, scene_id, now)
                await append_event(
                    session,
                    event_key=f"scene-start:{internal_id}:{scene_id}",
                    event_type="SCENE_STARTED",
                    user_id=internal_id,
                    occurred_at=now,
                    dimension=f"scene:{scene_id}",
                )
            elif is_scene_open and current.completed_at is not None:
                await append_event(
                    session,
                    event_key=f"revisit:{internal_id}:{scene_id}:{day}",
                    event_type="SCENE_REVISITED",
                    user_id=internal_id,
                    occurred_at=now,
                    dimension=f"scene:{scene_id}",
                )
            updated = await self._load_progress(session, user_id, scene_id)
            if updated is None:
                raise RuntimeError("Learning progress was not visible")
            return updated

    async def complete(
        self,
        user_id: str,
        scene_id: str,
        idempotency_key: str,
        now: datetime,
    ) -> CompletionResult:
        # 功能:幂等完成场景学习并记录完成事件与北京时间打卡
        # 参数:
        #     self: 当前SQL场景学习进度仓库实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     idempotency_key: 本次业务命令的幂等键,重复调用复用原操作
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:学习进度、首次完成标记与打卡日期
        learning_day = beijing_learning_date(now)
        async with self._session_factory() as session, session.begin():
            internal_id = await self._lock_user(session, user_id)
            current = await self._load_progress(session, user_id, scene_id, for_update=True)
            created = current is None or current.completed_at is None
            if current is None:
                await session.execute(
                    text(
                        "INSERT INTO learning_progress "
                        "(user_id, scene_id, source_type, position, last_client_sequence, "
                        "started_at, completed_at, last_learned_at) VALUES "
                        "(:user_id, :scene_id, 'SCENE', :position, 0, :now, :now, :now)"
                    ),
                    {
                        "user_id": internal_id,
                        "scene_id": scene_id,
                        "position": '{"entry_id":"start","offset":0}',
                        "now": _database_datetime(now),
                    },
                )
            elif created:
                await session.execute(
                    text(
                        "UPDATE learning_progress SET completed_at = :now, "
                        "last_learned_at = :now WHERE user_id = :user_id AND scene_id = :scene_id"
                    ),
                    {
                        "now": _database_datetime(now),
                        "user_id": internal_id,
                        "scene_id": scene_id,
                    },
                )
            if created:
                if current is None:
                    await self._record_open_start(session, internal_id, scene_id, now)
                    await append_event(
                        session,
                        event_key=f"scene-start:{internal_id}:{scene_id}",
                        event_type="SCENE_STARTED",
                        user_id=internal_id,
                        occurred_at=now,
                        dimension=f"scene:{scene_id}",
                    )
                open_config = await session.scalar(text("SELECT MAX(id) FROM open_scene_config"))
                is_open = await session.scalar(
                    text(
                        "SELECT COUNT(*) FROM open_scene_item i "
                        "JOIN scene s ON s.id=i.scene_id "
                        "WHERE i.config_id=:config AND s.public_id=:scene"
                    ),
                    {"config": open_config, "scene": scene_id},
                )
                if is_open:
                    await append_event(
                        session,
                        event_key=f"open-complete:{internal_id}:{open_config}:{scene_id}",
                        event_type="OPEN_SCENE_COMPLETED",
                        user_id=internal_id,
                        occurred_at=now,
                        dimension=f"scene:{scene_id}",
                    )
                    await append_event(
                        session,
                        event_key=f"open-learner:{internal_id}:{open_config}",
                        event_type="OPEN_LEARNER_STARTED",
                        user_id=internal_id,
                        occurred_at=now,
                    )
                    completed_open = await session.scalar(
                        text(
                            "SELECT COUNT(*) FROM open_scene_item i "
                            "JOIN scene s ON s.id=i.scene_id "
                            "JOIN learning_progress p ON p.scene_id=s.public_id "
                            "AND p.user_id=:user "
                            "WHERE i.config_id=:config AND p.completed_at IS NOT NULL"
                        ),
                        {"config": open_config, "user": internal_id},
                    )
                    if completed_open == 3:
                        first_open = await session.scalar(
                            text("SELECT occurred_at FROM analytics_event WHERE event_key=:key"),
                            {"key": f"open-learner:{internal_id}:{open_config}"},
                        )
                        await append_event(
                            session,
                            event_key=f"open-all:{internal_id}:{open_config}",
                            event_type="OPEN_ALL_COMPLETED",
                            user_id=internal_id,
                            occurred_at=now,
                            payload={
                                "cohort_day": beijing_learning_date(
                                    first_open.replace(tzinfo=UTC) if first_open else now
                                ).isoformat()
                            },
                        )
                limited = (
                    await session.execute(
                        text(
                            "SELECT le.public_id,le.granted_at,le.activated_at,le.expires_at,"
                            "cv.duration_days, "
                            "c.public_id AS campaign_id FROM limited_entitlement le "
                            "JOIN limited_campaign_version cv ON cv.id=le.campaign_version_id "
                            "JOIN limited_campaign c ON c.id=cv.campaign_id WHERE le.user_id=:user "
                            "AND le.status='ACTIVE' AND le.expires_at>:now AND NOT EXISTS ("
                            "SELECT 1 FROM limited_campaign_scene cs "
                            "JOIN scene ss ON ss.id=cs.scene_id "
                            "LEFT JOIN learning_progress lp ON lp.scene_id=ss.public_id "
                            "AND lp.user_id=:user "
                            "WHERE cs.campaign_version_id=cv.id AND (lp.completed_at IS NULL "
                            "OR lp.completed_at>le.expires_at)) FOR UPDATE"
                        ),
                        {"user": internal_id, "now": _database_datetime(now)},
                    )
                ).all()
                for entitlement in limited:
                    await append_event(
                        session,
                        event_key=f"limited-completed:{entitlement.public_id}",
                        event_type="LIMITED_COMPLETED",
                        user_id=internal_id,
                        occurred_at=now,
                        dimension=f"campaign:{entitlement.campaign_id}",
                        payload={
                            "mode": entitlement.duration_days,
                            "before_expiry": True,
                            "cohort_day": beijing_learning_date(
                                entitlement.granted_at.replace(tzinfo=UTC)
                            ).isoformat(),
                            "started_day": beijing_learning_date(
                                entitlement.activated_at.replace(tzinfo=UTC)
                            ).isoformat(),
                        },
                    )
                await append_event(
                    session,
                    event_key=f"scene-complete:{internal_id}:{scene_id}",
                    event_type="SCENE_COMPLETED",
                    user_id=internal_id,
                    occurred_at=now,
                    dimension=f"scene:{scene_id}",
                )
                await append_activity_events(session, internal_id, now)
                await session.execute(
                    text(
                        "INSERT INTO learning_completion_event "
                        "(user_id, scene_id, completed_at, idempotency_key) "
                        "VALUES (:user_id, :scene_id, :now, :idempotency_key)"
                    ),
                    {
                        "user_id": internal_id,
                        "scene_id": scene_id,
                        "now": _database_datetime(now),
                        "idempotency_key": idempotency_key,
                    },
                )
                await session.execute(
                    text(
                        "INSERT IGNORE INTO daily_checkin "
                        "(user_id, beijing_date, completion_source) "
                        "VALUES (:user_id, :learning_day, 'SCENE')"
                    ),
                    {"user_id": internal_id, "learning_day": learning_day},
                )
            progress = await self._load_progress(session, user_id, scene_id)
            if progress is None:
                raise RuntimeError("Completed progress was not visible")
            return CompletionResult(progress, created, learning_day)

    async def get_progress(self, user_id: str, scene_id: str) -> LearningProgress | None:
        # 功能:读取用户在指定场景的学习进度
        # 参数:
        #     self: 当前SQL场景学习进度仓库实例
        #     user_id: 当前操作所属用户的公开标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        # 返回:场景阅读位置、请求序号与学习时间;不存在或无候选时返回None
        async with self._session_factory() as session:
            return await self._load_progress(session, user_id, scene_id)

    async def list_history(self, user_id: str, limit: int = 100) -> list[LearningProgress]:
        # 功能:按最近学习时间查询用户场景进度历史
        # 参数:
        #     self: 当前SQL场景学习进度仓库实例
        #     user_id: 当前操作所属用户的公开标识
        #     limit: 本次查询或任务领取允许的最大记录数量
        # 返回:场景阅读位置、请求序号与学习时间集合
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT u.public_id, p.scene_id, p.source_type, p.position, "
                            "p.last_client_sequence, p.started_at, p.completed_at, "
                            "p.last_learned_at FROM learning_progress p "
                            "JOIN user_account u ON u.id = p.user_id "
                            "WHERE u.public_id = :public_id "
                            "ORDER BY p.last_learned_at DESC LIMIT :limit"
                        ),
                        {"public_id": user_id, "limit": limit},
                    )
                )
                .mappings()
                .all()
            )
        return [self._from_row(row) for row in rows]

    @staticmethod
    async def _record_open_start(
        session: AsyncSession,
        user_id: int,
        scene_id: str,
        now: datetime,
    ) -> None:
        # 功能:记录用户首次开始学习开放场景的统计事件
        # 参数:
        #     session: 异步数据库会话
        #     user_id: 业务数据库中的用户内部数值主键
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:无返回值。
        config = await session.scalar(
            text(
                "SELECT i.config_id FROM open_scene_item i JOIN scene s ON s.id=i.scene_id "
                "WHERE s.public_id=:scene AND i.config_id=(SELECT MAX(id) FROM open_scene_config)"
            ),
            {"scene": scene_id},
        )
        if config is not None:
            await append_event(
                session,
                event_key=f"open-learner:{user_id}:{config}",
                event_type="OPEN_LEARNER_STARTED",
                user_id=user_id,
                occurred_at=now,
            )

    @staticmethod
    async def _lock_user(session: AsyncSession, public_id: str) -> int:
        # 功能:锁定用户账号行并取得数据库内部主键
        # 参数:
        #     session: 异步数据库会话
        #     public_id: 当前操作所属用户账号的公开标识
        # 返回:已锁定用户账号的数据库内部主键
        internal_id = await session.scalar(
            text("SELECT id FROM user_account WHERE public_id = :public_id FOR UPDATE"),
            {"public_id": public_id},
        )
        if internal_id is None:
            raise AppError("USER_NOT_FOUND", "用户不存在", 404)
        return int(internal_id)

    @classmethod
    async def _load_progress(
        cls,
        session: AsyncSession,
        public_id: str,
        scene_id: str,
        *,
        for_update: bool = False,
    ) -> LearningProgress | None:
        # 功能:读取场景学习进度并按需锁定记录
        # 参数:
        #     cls: 当前SQLAlchemyLearningRepository类型,调用类级别的记录转换方法
        #     session: 异步数据库会话
        #     public_id: 当前操作所属用户账号的公开标识
        #     scene_id: 需要授权、学习或查询的场景公开标识
        #     for_update: 是否锁定查询记录以防止并发状态变更
        # 返回:场景阅读位置、请求序号与学习时间;不存在或无候选时返回None
        suffix = " FOR UPDATE" if for_update else ""
        row = (
            (
                await session.execute(
                    text(
                        "SELECT u.public_id, p.scene_id, p.source_type, p.position, "
                        "p.last_client_sequence, p.started_at, p.completed_at, "
                        "p.last_learned_at FROM learning_progress p "
                        "JOIN user_account u ON u.id = p.user_id "
                        "WHERE u.public_id = :public_id AND p.scene_id = :scene_id" + suffix
                    ),
                    {"public_id": public_id, "scene_id": scene_id},
                )
            )
            .mappings()
            .first()
        )
        return cls._from_row(row) if row is not None else None

    @staticmethod
    def _from_row(row: RowMapping) -> LearningProgress:
        # 功能:将数据库查询行转换为场景阅读位置、请求序号与学习时间
        # 参数:
        #     row: 查询返回的场景学习数据库字段映射
        # 返回:场景阅读位置、请求序号与学习时间
        raw_position = row["position"]
        payload = json.loads(raw_position) if isinstance(raw_position, str) else raw_position
        started_at = _utc_datetime(row["started_at"])
        last_learned_at = _utc_datetime(row["last_learned_at"])
        if started_at is None or last_learned_at is None:
            raise RuntimeError("Learning progress timestamps cannot be null")
        return LearningProgress(
            user_id=row["public_id"],
            scene_id=row["scene_id"],
            source_type=row["source_type"],
            position=ReadingPosition(payload["entry_id"], int(payload.get("offset", 0))),
            client_sequence=row["last_client_sequence"],
            started_at=started_at,
            completed_at=_utc_datetime(row["completed_at"]),
            last_learned_at=last_learned_at,
        )
