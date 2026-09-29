import os

import pytest

from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory
from juya_miniapp_api.modules.learning.admin_projection import (
    SQLAlchemyLearningOverviewRepository,
)


def _database_url() -> str:
    url = os.environ.get("JUYA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("JUYA_TEST_DATABASE_URL is required for MySQL integration tests")
    return url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)


@pytest.mark.asyncio
async def test_sql_learning_overview_returns_zero_counts_for_unknown_user() -> None:
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
