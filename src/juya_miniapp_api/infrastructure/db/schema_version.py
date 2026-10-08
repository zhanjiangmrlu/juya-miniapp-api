from collections.abc import Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def check_minimum_schema_version(
    factory: async_sessionmaker[AsyncSession], minimum_version: int
) -> Mapping[str, bool]:
    # 功能:检查数据库迁移版本是否达到服务要求
    # 参数:
    #     factory: 按请求创建异步数据库会话的工厂
    #     minimum_version: 服务运行要求的最低数据库迁移版本
    # 返回:依赖名称到就绪状态的映射
    async with factory() as session:
        current_version = await session.scalar(
            text("SELECT version FROM schema_version WHERE id = 1")
        )
    return {
        "mysql": current_version is not None,
        "schema": bool(current_version >= minimum_version),
    }
