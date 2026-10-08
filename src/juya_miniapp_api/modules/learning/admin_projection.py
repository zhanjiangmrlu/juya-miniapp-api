from dataclasses import dataclass
from datetime import date
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True, slots=True)
class LearningOverview:
    open_scene_completed_count: int
    learning_days: int
    favorite_count: int


class LearningOverviewRepository(Protocol):
    async def get(self, user_id: str) -> LearningOverview:
        # 功能:汇总开放场景完成数、学习天数与收藏数
        # 参数:
        #     self: 当前用户学习数量统计仓库实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:开放场景完成数、学习天数和收藏数
        ...


class InMemoryLearningOverviewRepository:
    def __init__(self) -> None:
        # 功能:初始化场景学习的InMemoryLearningOverviewRepository对象的状态存储
        # 参数:
        #     self: 当前场景学习的InMemoryLearningOverviewRepository实例
        # 返回:无返回值。
        self.open_scene_completion_events: list[tuple[str, str]] = []
        self.checkins: list[tuple[str, date]] = []
        self.favorite_entries: list[tuple[str, str]] = []

    async def get(self, user_id: str) -> LearningOverview:
        # 功能:汇总开放场景完成数、学习天数与收藏数
        # 参数:
        #     self: 当前场景学习的InMemoryLearningOverviewRepository实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:开放场景完成数、学习天数和收藏数
        return LearningOverview(
            open_scene_completed_count=len(
                {
                    scene_id
                    for completed_user_id, scene_id in self.open_scene_completion_events
                    if completed_user_id == user_id
                }
            ),
            learning_days=len(
                {
                    learning_day
                    for checked_user_id, learning_day in self.checkins
                    if checked_user_id == user_id
                }
            ),
            favorite_count=sum(
                1
                for favorite_user_id, _entry_id in self.favorite_entries
                if favorite_user_id == user_id
            ),
        )


class SQLAlchemyLearningOverviewRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        # 功能:初始化场景学习的SQLAlchemyLearningOverviewRepository对象并保存所需依赖与配置
        # 参数:
        #     self: 当前场景学习的SQLAlchemyLearningOverviewRepository实例
        #     session_factory: 创建数据库事务会话的异步工厂
        # 返回:无返回值。
        self._session_factory = session_factory

    async def get(self, user_id: str) -> LearningOverview:
        # 功能:汇总开放场景完成数、学习天数与收藏数
        # 参数:
        #     self: 当前场景学习的SQLAlchemyLearningOverviewRepository实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:开放场景完成数、学习天数和收藏数
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT "
                            "(SELECT COUNT(DISTINCT e.scene_id) "
                            "FROM learning_completion_event e "
                            "JOIN user_account completion_user ON completion_user.id = e.user_id "
                            "JOIN scene s ON s.public_id = e.scene_id "
                            "JOIN open_scene_item item ON item.scene_id = s.id "
                            "JOIN open_scene_config config ON config.id = item.config_id "
                            "WHERE completion_user.public_id = :public_id "
                            "AND config.version = (SELECT MAX(version) FROM open_scene_config)) "
                            "AS open_scene_completed_count, "
                            "(SELECT COUNT(DISTINCT d.beijing_date) FROM daily_checkin d "
                            "JOIN user_account checkin_user ON checkin_user.id = d.user_id "
                            "WHERE checkin_user.public_id = :public_id) AS learning_days, "
                            "(SELECT COUNT(*) FROM favorite_entry f "
                            "JOIN user_account favorite_user ON favorite_user.id = f.user_id "
                            "WHERE favorite_user.public_id = :public_id) AS favorite_count"
                        ),
                        {"public_id": user_id},
                    )
                )
                .mappings()
                .one()
            )
        return LearningOverview(
            open_scene_completed_count=int(row["open_scene_completed_count"] or 0),
            learning_days=int(row["learning_days"] or 0),
            favorite_count=int(row["favorite_count"] or 0),
        )
