import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory
from juya_miniapp_api.modules.accounts.repository import SQLAlchemyAccountRepository
from juya_miniapp_api.modules.accounts.service import AccountLifecycleService
from juya_miniapp_api.shared.ids import new_ulid

NOW = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)


def _database_url() -> str:
    url = os.environ.get("JUYA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("JUYA_TEST_DATABASE_URL is required for MySQL integration tests")
    return url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)


async def _noop_revoke(user_id: str, reason: str, now: datetime) -> None:
    del user_id, reason, now


@pytest.mark.asyncio
async def test_deletion_revoke_and_execute_are_mutually_exclusive() -> None:
    engine = create_engine(_database_url())
    factory = create_session_factory(engine)
    repository = SQLAlchemyAccountRepository(factory)
    service = AccountLifecycleService(repository, _noop_revoke)
    public_id = new_ulid(NOW)
    request_id: str | None = None
    try:
        async with factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO user_account (public_id, juya_number, status) "
                    "VALUES (:public_id, :juya_number, 'ACTIVE')"
                ),
                {"public_id": public_id, "juya_number": f"JY{public_id[-12:]}"},
            )
        request = await service.request_deletion(public_id, NOW - timedelta(days=7))
        request_id = request.id

        results = await asyncio.gather(
            service.revoke_deletion(public_id, NOW),
            service.execute_due_deletions(NOW),
            return_exceptions=True,
        )

        assert not any(isinstance(item, SQLAlchemyError) for item in results)
        assert any(not isinstance(item, Exception) for item in results)

        current = await repository.get_deletion(public_id, request.id)
        assert current.status in {"REVOKED", "DELETING"}
    finally:
        async with factory() as session, session.begin():
            if request_id is not None:
                await session.execute(
                    text("DELETE FROM miniapp_outbox WHERE aggregate_id = :request_id"),
                    {"request_id": request_id},
                )
            await session.execute(
                text("DELETE FROM user_account WHERE public_id = :public_id"),
                {"public_id": public_id},
            )
        await engine.dispose()
