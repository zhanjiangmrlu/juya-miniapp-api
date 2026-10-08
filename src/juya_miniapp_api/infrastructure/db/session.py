from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    # 功能:创建连接业务数据库的异步SQLAlchemy引擎
    # 参数:
    #     database_url: 业务数据库异步连接地址
    #     echo: 是否输出SQLAlchemy执行的SQL日志
    # 返回:异步数据库连接引擎
    return create_async_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # 功能:创建异步数据库会话工厂
    # 参数:
    #     engine: 异步SQLAlchemy数据库连接引擎
    # 返回:按调用创建AsyncSession的会话工厂
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    # 功能:提供自动提交或回滚并关闭的异步数据库会话
    # 参数:
    #     factory: 按请求创建异步数据库会话的工厂
    # 返回:异步产出事务会话;退出时自动提交或回滚并关闭
    async with factory() as session:
        yield session
