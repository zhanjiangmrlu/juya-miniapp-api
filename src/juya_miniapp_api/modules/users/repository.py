from datetime import UTC, date, datetime
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from juya_miniapp_api.modules.checkins.service import beijing_learning_date, summarize_checkins
from juya_miniapp_api.modules.users.models import UserProfile
from juya_miniapp_api.shared.errors import AppError


class UserRepository(Protocol):
    async def pending_deletion(self, public_id: str) -> dict[str, object] | None:
        # 功能:查询用户等待生效或正在执行的注销申请
        # 参数:
        #     self: 当前用户资料存储仓库实例
        #     public_id: 当前操作所属用户账号的公开标识
        # 返回:注销申请公开标识、状态与时间字段;无申请时为None
        ...
    async def get_profile(self, public_id: str) -> UserProfile | None:
        # 功能:读取用户账号与昵称头像资料
        # 参数:
        #     self: 当前用户资料存储仓库实例
        #     public_id: 当前操作所属用户账号的公开标识
        # 返回:用户账号资料与昵称头像;不存在或无候选时返回None
        ...

    async def update_profile(
        self,
        public_id: str,
        nickname: str | None,
        avatar_object_key: str | None,
        now: datetime,
    ) -> UserProfile:
        # 功能:保存用户昵称与头像对象键并读取更新后的资料
        # 参数:
        #     self: 当前用户资料存储仓库实例
        #     public_id: 当前操作所属用户账号的公开标识
        #     nickname: 待保存的用户昵称; None表示未提供或清空昵称
        #     avatar_object_key: 用户头像在OSS中的对象键
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:用户账号资料与昵称头像
        ...

    async def search_profiles(
        self, *, juya_number: str | None, nickname: str | None, limit: int
    ) -> list[UserProfile]:
        # 功能:按句芽编号或昵称查询用户资料
        # 参数:
        #     self: 当前用户资料存储仓库实例
        #     juya_number: 用户对外展示的句芽编号,亦可作为搜索条件
        #     nickname: 需要匹配的昵称搜索文本; None表示不按昵称筛选
        #     limit: 本次查询或任务领取允许的最大记录数量
        # 返回:用户账号资料与昵称头像集合
        ...


class InMemoryUserRepository:
    async def pending_deletion(self, public_id: str) -> dict[str, object] | None:
        # 功能:查询用户等待生效或正在执行的注销申请
        # 参数:
        #     self: 当前用户资料的InMemoryUserRepository实例
        #     public_id: 当前操作所属用户账号的公开标识
        # 返回:注销申请公开标识、状态与时间字段;无申请时为None
        return None

    def __init__(self) -> None:
        # 功能:初始化用户资料的InMemoryUserRepository对象的状态存储
        # 参数:
        #     self: 当前用户资料的InMemoryUserRepository实例
        # 返回:无返回值。
        self.profiles: dict[str, UserProfile] = {}

    def add(
        self,
        public_id: str,
        juya_number: str,
        *,
        nickname: str | None = None,
        avatar_object_key: str | None = None,
        status: str = "ACTIVE",
    ) -> None:
        # 功能:向内存用户仓库添加初始账号资料
        # 参数:
        #     self: 当前用户资料的InMemoryUserRepository实例
        #     public_id: 当前操作所属用户账号的公开标识
        #     juya_number: 用户对外展示的句芽编号,亦可作为搜索条件
        #     nickname: 待保存的用户昵称; None表示未提供或清空昵称
        #     avatar_object_key: 用户头像在OSS中的对象键
        #     status: 初始化用户账号时保存的账号业务状态
        # 返回:无返回值。
        self.profiles[public_id] = UserProfile(
            public_id, juya_number, status, nickname, avatar_object_key
        )

    async def get_profile(self, public_id: str) -> UserProfile | None:
        # 功能:读取用户账号与昵称头像资料
        # 参数:
        #     self: 当前用户资料的InMemoryUserRepository实例
        #     public_id: 当前操作所属用户账号的公开标识
        # 返回:用户账号资料与昵称头像;不存在或无候选时返回None
        return self.profiles.get(public_id)

    async def update_profile(
        self,
        public_id: str,
        nickname: str | None,
        avatar_object_key: str | None,
        now: datetime,
    ) -> UserProfile:
        # 功能:保存用户昵称与头像对象键并读取更新后的资料
        # 参数:
        #     self: 当前用户资料的InMemoryUserRepository实例
        #     public_id: 当前操作所属用户账号的公开标识
        #     nickname: 待保存的用户昵称; None表示未提供或清空昵称
        #     avatar_object_key: 用户头像在OSS中的对象键
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:用户账号资料与昵称头像
        del now
        current = self.profiles.get(public_id)
        if current is None:
            raise AppError("USER_NOT_FOUND", "用户不存在", 404)
        updated = UserProfile(
            current.public_id,
            current.juya_number,
            current.status,
            nickname,
            avatar_object_key,
            current.created_at,
            current.last_active_at,
        )
        self.profiles[public_id] = updated
        return updated

    async def search_profiles(
        self, *, juya_number: str | None, nickname: str | None, limit: int
    ) -> list[UserProfile]:
        # 功能:按句芽编号或昵称查询用户资料
        # 参数:
        #     self: 当前用户资料的InMemoryUserRepository实例
        #     juya_number: 用户对外展示的句芽编号,亦可作为搜索条件
        #     nickname: 需要匹配的昵称搜索文本; None表示不按昵称筛选
        #     limit: 本次查询或任务领取允许的最大记录数量
        # 返回:用户账号资料与昵称头像集合
        profiles = list(self.profiles.values())
        if juya_number:
            profiles = [item for item in profiles if item.juya_number.startswith(juya_number)]
        if nickname:
            profiles = [item for item in profiles if nickname in (item.nickname or "")]
        return profiles[:limit]


def _utc(value: datetime | None) -> datetime | None:
    # 功能:将数据库时间统一为带UTC时区的时间
    # 参数:
    #     value: 待转换时区的数据库或业务时间;空值保留为空
    # 返回:带UTC时区的时间;原值为空时返回None
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SQLAlchemyUserRepository:
    async def pending_deletion(self, public_id: str) -> dict[str, object] | None:
        # 功能:查询用户等待生效或正在执行的注销申请
        # 参数:
        #     self: 当前用户资料的SQLAlchemyUserRepository实例
        #     public_id: 当前操作所属用户账号的公开标识
        # 返回:注销申请公开标识、状态与时间字段;无申请时为None
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT d.status,d.effective_at FROM account_deletion_request d "
                            "JOIN user_account u ON u.id=d.user_id WHERE u.public_id=:user "
                            "AND d.status='PENDING' ORDER BY d.id DESC LIMIT 1"
                        ),
                        {"user": public_id},
                    )
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def learning_achievements(self, public_id: str) -> dict[str, int]:
        # 功能:统计用户已完成场景、连续学习天数与单词短语收藏数量
        # 参数:
        #     self: 当前用户资料的SQLAlchemyUserRepository实例
        #     public_id: 当前操作所属用户账号的公开标识
        # 返回:已完成场景数、连续和累计学习天数、单词与短语收藏数
        async with self._session_factory() as session:
            completed = await session.scalar(
                text(
                    "SELECT COUNT(*) FROM learning_progress p "
                    "JOIN user_account u ON u.id=p.user_id "
                    "WHERE u.public_id=:user AND p.completed_at IS NOT NULL"
                ),
                {"user": public_id},
            )
            favorites = (
                (
                    await session.execute(
                        text(
                            "SELECT f.entry_type,COUNT(*) AS total FROM favorite_entry f "
                            "JOIN user_account u ON u.id=f.user_id "
                            "WHERE u.public_id=:user GROUP BY f.entry_type"
                        ),
                        {"user": public_id},
                    )
                )
                .mappings()
                .all()
            )
            days: list[date] = list(
                (
                    await session.execute(
                        text(
                            "SELECT d.beijing_date FROM daily_checkin d "
                            "JOIN user_account u ON u.id=d.user_id "
                            "WHERE u.public_id=:user ORDER BY d.beijing_date"
                        ),
                        {"user": public_id},
                    )
                )
                .scalars()
                .all()
            )
        counts = {row["entry_type"]: int(row["total"]) for row in favorites}
        summary = summarize_checkins(days, today=beijing_learning_date(datetime.now(UTC)))
        return {
            "completed_scenes": int(completed or 0),
            "streak_days": summary.current_streak,
            "learning_days": summary.total_days,
            "favorite_vocabulary": counts.get("VOCABULARY", 0),
            "favorite_phrases": counts.get("PHRASE", 0),
        }

    async def count_open_completions(self, public_id: str) -> int:
        # 功能:统计用户去重后的开放场景完成数量
        # 参数:
        #     self: 当前用户资料的SQLAlchemyUserRepository实例
        #     public_id: 当前操作所属用户账号的公开标识
        # 返回:去重后的开放场景完成数量
        async with self._session_factory() as session:
            count = await session.scalar(
                text(
                    "SELECT COUNT(DISTINCT p.scene_id) FROM learning_progress p "
                    "JOIN user_account u ON u.id=p.user_id JOIN scene s ON s.public_id=p.scene_id "
                    "JOIN open_scene_item i ON i.scene_id=s.id "
                    "WHERE u.public_id=:user AND p.completed_at IS NOT NULL "
                    "AND i.config_id=(SELECT MAX(id) FROM open_scene_config)"
                ),
                {"user": public_id},
            )
            return int(count or 0)

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        # 功能:初始化用户资料的SQLAlchemyUserRepository对象并保存所需依赖与配置
        # 参数:
        #     self: 当前用户资料的SQLAlchemyUserRepository实例
        #     session_factory: 创建数据库事务会话的异步工厂
        # 返回:无返回值。
        self._session_factory = session_factory

    async def get_profile(self, public_id: str) -> UserProfile | None:
        # 功能:读取用户账号与昵称头像资料
        # 参数:
        #     self: 当前用户资料的SQLAlchemyUserRepository实例
        #     public_id: 当前操作所属用户账号的公开标识
        # 返回:用户账号资料与昵称头像;不存在或无候选时返回None
        async with self._session_factory() as session:
            return await self._load(session, public_id)

    async def update_profile(
        self,
        public_id: str,
        nickname: str | None,
        avatar_object_key: str | None,
        now: datetime,
    ) -> UserProfile:
        # 功能:保存用户昵称与头像对象键并读取更新后的资料
        # 参数:
        #     self: 当前用户资料的SQLAlchemyUserRepository实例
        #     public_id: 当前操作所属用户账号的公开标识
        #     nickname: 待保存的用户昵称; None表示未提供或清空昵称
        #     avatar_object_key: 用户头像在OSS中的对象键
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:用户账号资料与昵称头像
        database_now = now.astimezone(UTC).replace(tzinfo=None) if now.tzinfo else now
        async with self._session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE user_profile p JOIN user_account u ON u.id = p.user_id "
                    "SET p.nickname = :nickname, p.avatar_object_key = :avatar, "
                    "p.updated_at = :now WHERE u.public_id = :public_id"
                ),
                {
                    "nickname": nickname,
                    "avatar": avatar_object_key,
                    "now": database_now,
                    "public_id": public_id,
                },
            )
            profile = await self._load(session, public_id)
            if profile is None:
                raise AppError("USER_NOT_FOUND", "用户不存在", 404)
            return profile

    async def search_profiles(
        self, *, juya_number: str | None, nickname: str | None, limit: int
    ) -> list[UserProfile]:
        # 功能:按句芽编号或昵称查询用户资料
        # 参数:
        #     self: 当前用户资料的SQLAlchemyUserRepository实例
        #     juya_number: 用户对外展示的句芽编号,亦可作为搜索条件
        #     nickname: 需要匹配的昵称搜索文本; None表示不按昵称筛选
        #     limit: 本次查询或任务领取允许的最大记录数量
        # 返回:用户账号资料与昵称头像集合
        conditions: list[str] = []
        parameters: dict[str, object] = {"limit": limit}
        if juya_number:
            conditions.append("u.juya_number LIKE :juya_number")
            parameters["juya_number"] = f"{juya_number}%"
        if nickname:
            conditions.append("p.nickname LIKE :nickname")
            parameters["nickname"] = f"%{nickname}%"
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT u.public_id, u.juya_number, u.status, u.created_at, "
                            "u.last_active_at, p.nickname, p.avatar_object_key "
                            "FROM user_account u JOIN user_profile p ON p.user_id = u.id"
                            + where
                            + " ORDER BY u.created_at DESC LIMIT :limit"
                        ),
                        parameters,
                    )
                )
                .mappings()
                .all()
            )
        return [self._from_row(row) for row in rows]

    @classmethod
    async def _load(cls, session: AsyncSession, public_id: str) -> UserProfile | None:
        # 功能:在当前数据库会话内读取用户资料
        # 参数:
        #     cls: 当前SQLAlchemyUserRepository类型,调用类级别的记录转换方法
        #     session: 异步数据库会话
        #     public_id: 当前操作所属用户账号的公开标识
        # 返回:用户账号资料与昵称头像;不存在或无候选时返回None
        row = (
            (
                await session.execute(
                    text(
                        "SELECT u.public_id, u.juya_number, u.status, u.created_at, "
                        "u.last_active_at, p.nickname, p.avatar_object_key "
                        "FROM user_account u JOIN user_profile p ON p.user_id = u.id "
                        "WHERE u.public_id = :public_id"
                    ),
                    {"public_id": public_id},
                )
            )
            .mappings()
            .first()
        )
        return cls._from_row(row) if row is not None else None

    @staticmethod
    def _from_row(mapping: RowMapping) -> UserProfile:
        # 功能:将数据库查询行转换为用户账号资料与昵称头像
        # 参数:
        #     mapping: 查询返回的用户资料数据库字段映射
        # 返回:用户账号资料与昵称头像
        return UserProfile(
            mapping["public_id"],
            mapping["juya_number"],
            mapping["status"],
            mapping["nickname"],
            mapping["avatar_object_key"],
            _utc(mapping["created_at"]),
            _utc(mapping["last_active_at"]),
        )
