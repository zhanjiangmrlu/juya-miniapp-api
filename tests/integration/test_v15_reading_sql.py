import asyncio
import os
from datetime import UTC, datetime, timedelta
from time import monotonic

import pytest
from sqlalchemy import text

from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory
from juya_miniapp_api.modules.short_reading.domain import ReadingPosition
from juya_miniapp_api.modules.short_reading.sql_repository import SQLAlchemyReadingRepository
from juya_miniapp_api.shared.errors import AppError
from juya_miniapp_api.shared.ids import new_ulid


async def wait_for_user_locks(session, count):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        waiting = await session.scalar(
            text(
                "SELECT COUNT(*) FROM performance_schema.data_lock_waits w "
                "JOIN performance_schema.data_locks l "
                "ON l.ENGINE_LOCK_ID=w.REQUESTING_ENGINE_LOCK_ID "
                "WHERE l.OBJECT_SCHEMA=DATABASE() AND l.OBJECT_NAME='user_account'"
            )
        )
        if waiting >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Concurrent reading commands did not reach the locked user row")


@pytest.mark.asyncio
async def test_mysql_reading_is_independent_idempotent_and_rechecks_facts_in_transaction() -> None:
    url = os.getenv("JUYA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("isolated MySQL required")
    engine = create_engine(url.replace("mysql+pymysql://", "mysql+asyncmy://"))
    sessions = create_session_factory(engine)
    now = datetime(2026, 10, 11, tzinfo=UTC)
    user = new_ulid(now)
    denied = False

    async def guard(session, owner, content_id, version):
        assert session.in_transaction()
        if denied:
            raise AppError("MODULE_DISABLED", "关闭", 403)

    try:
        async with sessions() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO user_account(public_id,juya_number,status) "
                    "VALUES(:id,:number,'ACTIVE')"
                ),
                {"id": user, "number": "JY" + user[-12:]},
            )
        repo = SQLAlchemyReadingRepository(sessions, guard)
        paragraphs = {"p1": 100}
        position = ReadingPosition(
            content_version=1, paragraph_id="p1", offset=5, client_sequence=5
        )
        await repo.save(user, "reading", position, now)
        # Lower and duplicate sequence never overwrite the accepted position.
        for sequence in (4, 5):
            stale = await repo.save(
                user,
                "reading",
                ReadingPosition(
                    content_version=1, paragraph_id="p1", offset=1, client_sequence=sequence
                ),
                now + timedelta(seconds=2),
            )
            assert stale.offset == 5 and stale.client_sequence == 5
        first = await repo.complete(user, "reading", "done", 1, paragraphs, now)
        assert first.created and first.offset == 5
        assert await repo.complete(user, "reading", "done", 1, paragraphs, now) == first
        assert not (await repo.complete(user, "reading", "different", 1, paragraphs, now)).created
        with pytest.raises(AppError) as conflict:
            await repo.complete(user, "reading", "done", 2, paragraphs, now)
        assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
        changed = await repo.open(user, "reading", 2, paragraphs, now)
        assert changed.completed_at == first.completed_at == now
        assert changed.saved_content_version == 2
        assert changed.paragraph_id is None and changed.client_sequence == 0
        reset = await repo.save(
            user,
            "reading",
            ReadingPosition(content_version=2, paragraph_id="p1", offset=2, client_sequence=1),
            now,
        )
        assert reset.client_sequence == 1 and reset.completed_at == now
        # Same key is independent for a different stable content object.
        assert (await repo.complete(user, "reading-other", "done", 1, paragraphs, now)).created
        denied = True
        # Committed receipt can replay without obtaining full text entitlement again.
        assert await repo.replay_completion(user, "reading", "done", 1) == first
        assert await repo.complete(user, "reading", "done", 1, paragraphs, now) == first
        with pytest.raises(AppError):
            await repo.save(user, "reading", position, now)
        with pytest.raises(AppError):
            await repo.complete(user, "reading", "fresh-command", 2, paragraphs, now)
        async with sessions() as session:
            for table in ["learning_progress", "learning_completion_event", "daily_checkin"]:
                assert (
                    await session.scalar(
                        text(
                            f"SELECT COUNT(*) FROM {table} p JOIN user_account u ON p.user_id=u.id "
                            "WHERE u.public_id=:user"
                        ),
                        {"user": user},
                    )
                    == 0
                )
    finally:
        async with sessions() as session, session.begin():
            await session.execute(
                text("DELETE FROM user_account WHERE public_id=:user"), {"user": user}
            )
        await engine.dispose()


@pytest.mark.asyncio
async def test_mysql_concurrent_sequences_open_and_complete_preserve_latest_position() -> None:
    url = os.getenv("JUYA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("isolated MySQL required")
    engine = create_engine(url.replace("mysql+pymysql://", "mysql+asyncmy://"))
    sessions = create_session_factory(engine)
    now = datetime(2026, 10, 11, tzinfo=UTC)
    user = new_ulid(now)

    async def guard(session, owner, content_id, version):
        assert session.in_transaction()

    try:
        async with sessions() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO user_account(public_id,juya_number,status) "
                    "VALUES(:id,:number,'ACTIVE')"
                ),
                {"id": user, "number": "JY" + user[-12:]},
            )
        repo = SQLAlchemyReadingRepository(sessions, guard)
        paragraphs = {"p1": 100}
        await repo.save(
            user,
            "reading",
            ReadingPosition(content_version=1, paragraph_id="p1", offset=1, client_sequence=1),
            now,
        )
        # Queue both commands on an actual locked user row; save wins before open/complete.
        async with sessions() as blocker, blocker.begin():
            await blocker.execute(
                text("SELECT id FROM user_account WHERE public_id=:user FOR UPDATE"), {"user": user}
            )
            saved = asyncio.create_task(
                repo.save(
                    user,
                    "reading",
                    ReadingPosition(
                        content_version=1, paragraph_id="p1", offset=9, client_sequence=9
                    ),
                    now,
                )
            )
            await wait_for_user_locks(blocker, 1)
            opened = asyncio.create_task(repo.open(user, "reading", 1, paragraphs, now))
            completed = asyncio.create_task(
                repo.complete(user, "reading", "concurrent", 1, paragraphs, now)
            )
            await wait_for_user_locks(blocker, 3)
        await saved
        assert (await opened).offset == 9
        first = await completed
        assert first.offset == 9 and first.client_sequence == 9 and first.created
        results = await asyncio.gather(
            *[
                repo.save(
                    user,
                    "reading",
                    ReadingPosition(
                        content_version=1,
                        paragraph_id="p1",
                        offset=sequence,
                        client_sequence=sequence,
                    ),
                    now,
                )
                for sequence in [12, 10, 15, 13, 11, 14]
            ]
        )
        assert max(row.client_sequence for row in results) == 15
        stored = await repo.get(user, "reading")
        assert stored.offset == 15 and stored.client_sequence == 15
        assert stored.completed_at == now
    finally:
        async with sessions() as session, session.begin():
            await session.execute(
                text("DELETE FROM user_account WHERE public_id=:user"), {"user": user}
            )
        await engine.dispose()
