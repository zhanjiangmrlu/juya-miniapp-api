import os

import pytest

from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory
from juya_miniapp_api.modules.learning.admin_projection import (
    SQLAlchemyLearningOverviewRepository,
)


def _database_url() -> str:
    # 功能:在测试中读取集成测试的隔离数据库连接配置
    # 参数:
    #     无形参。
    # 返回:集成测试数据库连接地址
    url = os.environ.get("JUYA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("JUYA_TEST_DATABASE_URL is required for MySQL integration tests")
    return url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)


@pytest.mark.asyncio
async def test_sql_learning_overview_returns_zero_counts_for_unknown_user() -> None:
    # 功能:验证SQL学习统计对不存在用户返回零数量
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    engine = create_engine(_database_url())
    try:
        overview = await SQLAlchemyLearningOverviewRepository(create_session_factory(engine)).get(
            "missing-user"
        )

        assert overview.open_scene_completed_count == 0
        assert overview.learning_days == 0
        assert overview.favorite_count == 0
    finally:
        await engine.dispose()
