from datetime import date
from typing import cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class SQLAlchemyCheckinRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        # 功能:初始化SQL用户打卡日期仓库并保存所需依赖与配置
        # 参数:
        #     self: 当前SQL用户打卡日期仓库实例
        #     session_factory: 创建数据库事务会话的异步工厂
        # 返回:无返回值。
        self._session_factory = session_factory

    async def list_days(self, user_id: str) -> list[date]:
        # 功能:查询用户已打卡的北京时间日期集合
        # 参数:
        #     self: 当前SQL用户打卡日期仓库实例
        #     user_id: 当前操作所属用户的公开标识
        # 返回:用户去重后的北京时间打卡日期列表
        async with self._session_factory() as session:
            values = cast(
                list[date],
                (
                    await session.execute(
                        text(
                            "SELECT d.beijing_date FROM daily_checkin d "
                            "JOIN user_account u ON u.id = d.user_id "
                            "WHERE u.public_id = :public_id ORDER BY d.beijing_date"
                        ),
                        {"public_id": user_id},
                    )
                )
                .scalars()
                .all(),
            )
        return values
