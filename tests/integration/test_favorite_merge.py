import asyncio
import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory
from juya_miniapp_api.modules.favorites.repository import SQLAlchemyFavoriteRepository
from juya_miniapp_api.modules.favorites.service import FavoriteService
from juya_miniapp_api.shared.ids import new_ulid

NOW = datetime(2026, 9, 28, 22, 30, tzinfo=UTC)


def _database_url() -> str:
    url = os.environ.get("JUYA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("JUYA_TEST_DATABASE_URL is required for MySQL integration tests")
    return url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)


@pytest.mark.asyncio
async def test_concurrent_favorite_merges_sources_and_review_completion_is_idempotent() -> None:
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

        service = FavoriteService(SQLAlchemyFavoriteRepository(factory))
        favorites = await asyncio.gather(
            *(
                service.favorite(
                    public_id,
                    "VOCABULARY",
                    "  Hello   WORLD ",
                    "entry-1",
                    f"scene-{index}",
                    f"Snapshot {index}",
                    f"sentence-{index}",
                    NOW,
                )
                for index in range(20)
            )
        )
        assert len({item.public_id for item in favorites}) == 1

        review = await service.create_review(public_id, list(range(500)), "review-create-1", NOW)
        completions = await asyncio.gather(
            *(
                service.complete_review(public_id, review.id, f"done-{index}", NOW)
                for index in range(20)
            )
        )
        assert sum(item.created for item in completions) == 1

        async with factory() as session:
            favorite_count = await session.scalar(
                text(
                    "SELECT COUNT(*) FROM favorite_entry f "
                    "JOIN user_account u ON u.id = f.user_id "
                    "WHERE u.public_id = :public_id"
                ),
                {"public_id": public_id},
            )
            source_count = await session.scalar(
                text(
                    "SELECT COUNT(*) FROM favorite_source s "
                    "JOIN favorite_entry f ON f.id = s.favorite_id "
                    "JOIN user_account u ON u.id = f.user_id "
                    "WHERE u.public_id = :public_id"
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
        assert favorite_count == 1
        assert source_count == 20
        assert checkin_count == 1

        await service.delete(public_id, favorites[0].public_id)
        async with factory() as session:
            remaining_sources = await session.scalar(text("SELECT COUNT(*) FROM favorite_source"))
        assert remaining_sources == 0
    finally:
        async with factory() as session, session.begin():
            await session.execute(
                text("DELETE FROM user_account WHERE public_id = :public_id"),
                {"public_id": public_id},
            )
        await engine.dispose()
