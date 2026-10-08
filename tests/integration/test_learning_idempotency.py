import asyncio
import json
import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory
from juya_miniapp_api.modules.learning.domain import ReadingPosition
from juya_miniapp_api.modules.learning.repository import SQLAlchemyLearningRepository
from juya_miniapp_api.modules.learning.service import LearningService
from juya_miniapp_api.shared.ids import new_ulid

NOW = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)


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
async def test_concurrent_progress_and_completion_are_monotonic_and_idempotent() -> None:
    # 功能:验证并发进度保持单调且学习完成保持幂等
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    engine = create_engine(_database_url())
    factory = create_session_factory(engine)
    public_id = new_ulid(NOW)
    juya_number = f"JY{public_id[-12:]}"
    try:
        async with factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO user_account (public_id, juya_number, status) "
                    "VALUES (:public_id, :juya_number, 'ACTIVE')"
                ),
                {"public_id": public_id, "juya_number": juya_number},
            )
            user_id = await session.scalar(text("SELECT LAST_INSERT_ID()"))
            await session.execute(
                text("INSERT INTO user_profile (user_id, source) VALUES (:user_id, 'WECHAT')"),
                {"user_id": user_id},
            )

        service = LearningService(SQLAlchemyLearningRepository(factory))
        progress_results = await asyncio.gather(
            *(
                service.save_progress(
                    public_id,
                    "scene-concurrent",
                    sequence,
                    ReadingPosition(f"entry-{sequence}", sequence),
                    NOW,
                )
                for sequence in range(1, 21)
            )
        )
        assert max(item.client_sequence for item in progress_results) == 20

        completion_results = await asyncio.gather(
            *(
                service.complete(
                    public_id,
                    "scene-concurrent",
                    f"complete-{index}",
                    NOW,
                )
                for index in range(30)
            )
        )
        assert sum(item.created for item in completion_results) == 1

        async with factory() as session:
            progress = (
                (
                    await session.execute(
                        text(
                            "SELECT p.last_client_sequence, p.position, p.completed_at "
                            "FROM learning_progress p JOIN user_account u ON u.id = p.user_id "
                            "WHERE u.public_id = :public_id AND p.scene_id = 'scene-concurrent'"
                        ),
                        {"public_id": public_id},
                    )
                )
                .mappings()
                .one()
            )
            completion_count = await session.scalar(
                text(
                    "SELECT COUNT(*) FROM learning_completion_event e "
                    "JOIN user_account u ON u.id = e.user_id "
                    "WHERE u.public_id = :public_id AND e.scene_id = 'scene-concurrent'"
                ),
                {"public_id": public_id},
            )
            checkin_count = await session.scalar(
                text(
                    "SELECT COUNT(*) FROM daily_checkin d "
                    "JOIN user_account u ON u.id = d.user_id "
                    "WHERE u.public_id = :public_id"
                ),
                {"public_id": public_id},
            )
        assert progress["last_client_sequence"] == 20
        position = json.loads(progress["position"])
        assert position["entry_id"] == "entry-20"
        assert progress["completed_at"] is not None
        assert completion_count == 1
        assert checkin_count == 1
    finally:
        async with factory() as session, session.begin():
            await session.execute(
                text("DELETE FROM user_account WHERE public_id = :public_id"),
                {"public_id": public_id},
            )
        await engine.dispose()
