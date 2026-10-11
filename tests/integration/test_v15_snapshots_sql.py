import asyncio
import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory
from juya_miniapp_api.modules.favorites.snapshots import (
    ConfirmedSnapshot,
    SnapshotReference,
    SnapshotService,
)
from juya_miniapp_api.modules.favorites.snapshots_sql import SQLAlchemySnapshotRepository
from juya_miniapp_api.shared.errors import AppError
from juya_miniapp_api.shared.ids import new_ulid

NOW = datetime(2026, 10, 11, tzinfo=UTC)


class Authority:
    async def confirm_snapshot(self, user: str, ref: SnapshotReference) -> ConfirmedSnapshot:
        return ConfirmedSnapshot(
            reference=ref,
            revision_id="revision",
            english="medium",
            meaning_zh=ref.sense_id,
            explanation="说明",
            source_sentence="A medium coffee.",
            source_title="咖啡",
            source_locator={"sentence_id": "sentence"},
        )


@pytest.mark.asyncio
async def test_mysql_snapshot_concurrency_cancel_revision_events_and_atomic_guard() -> None:
    url = os.getenv("JUYA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("isolated MySQL required")
    engine = create_engine(url.replace("mysql+pymysql://", "mysql+asyncmy://"))
    sessions = create_session_factory(engine)
    user = new_ulid(NOW)
    deny = False

    async def guard(session, owner, data):
        assert owner == user
        if deny:
            raise AppError("CONTENT_VERSION_CONFLICT", "发生变化", 409)

    try:
        async with sessions() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO user_account(public_id,juya_number,status) "
                    "VALUES(:id,:number,'ACTIVE')"
                ),
                {"id": user, "number": "JY" + user[-12:]},
            )
        repo = SQLAlchemySnapshotRepository(sessions, guard)
        service = SnapshotService(repo, Authority())
        ref = SnapshotReference(
            content_type="word_card",
            content_id="content",
            content_version=1,
            entry_type="word",
            entry_id="medium",
            sense_id="cup",
            source_id="source",
        )
        rows = await asyncio.gather(
            *[service.create(user, ref, "create-" + str(i), NOW) for i in range(10)]
        )
        assert len({row.favorite_id for row in rows}) == 1
        first = rows[0]
        other = await service.create(
            user, ref.model_copy(update={"sense_id": "size"}), "other", NOW
        )
        assert first.favorite_id != other.favorite_id
        await service.cancel(user, first.favorite_id, "cancel", NOW)
        await service.cancel(user, first.favorite_id, "cancel-again", NOW)
        restored = await service.create(
            user, ref.model_copy(update={"content_version": 2}), "restore", NOW
        )
        assert restored.snapshot_version == 2
        assert restored.favorite_id == first.favorite_id
        assert (await repo.get(user, restored.favorite_id)).saved_content_version == 2
        assert len((await service.page(user, "word", None, 100)).items) == 2
        review = await repo.create_review(
            user, [restored.favorite_id, other.favorite_id], "review", NOW
        )
        assert review.snapshot_versions == [2, 1]
        from juya_miniapp_api.modules.learning.admin_projection import (
            SQLAlchemyLearningOverviewRepository,
        )
        from juya_miniapp_api.modules.users.repository import SQLAlchemyUserRepository

        assert (await SQLAlchemyUserRepository(sessions).learning_achievements(user))[
            "favorite_vocabulary"
        ] == 2
        assert (await SQLAlchemyLearningOverviewRepository(sessions).get(user)).favorite_count == 2
        completions = await asyncio.gather(
            *[repo.complete_review(user, review.id, "done-" + str(i), NOW) for i in range(10)]
        )
        assert sum(row["created"] for row in completions) == 1
        changed_review = await repo.create_review(user, [other.favorite_id], "changed-review", NOW)
        await service.cancel(user, other.favorite_id, "cancel-other", NOW)
        with pytest.raises(AppError):
            await repo.complete_review(user, changed_review.id, "changed-done", NOW)
        deny = True
        with pytest.raises(AppError):
            await service.create(
                user, ref.model_copy(update={"source_id": "denied"}), "denied", NOW
            )
        async with sessions() as session:
            assert (
                await session.scalar(
                    text(
                        "SELECT COUNT(*) FROM favorite_snapshot f JOIN user_account u ON "
                        "u.id=f.user_id "
                        "WHERE u.public_id=:user"
                    ),
                    {"user": user},
                )
                == 2
            )
            assert (
                await session.scalar(
                    text(
                        "SELECT COUNT(*) FROM miniapp_outbox WHERE aggregate_id IN (:first,:other)"
                    ),
                    {"first": first.favorite_id, "other": other.favorite_id},
                )
                == 5
            )
        from juya_miniapp_api.modules.accounts.repository import SQLAlchemyAccountRepository

        await SQLAlchemyAccountRepository(sessions).clear_learning_data(user)
        assert (await service.page(user, None, None, 100)).items == []
        async with sessions() as session:
            assert (
                await session.scalar(
                    text(
                        "SELECT COUNT(*) FROM favorite_resource_reference "
                        "WHERE favorite_id IN (:first,:other) AND active=1"
                    ),
                    {"first": first.favorite_id, "other": other.favorite_id},
                )
                == 0
            )
            assert (
                await session.scalar(
                    text("SELECT COUNT(*) FROM miniapp_outbox WHERE aggregate_id=:first"),
                    {"first": first.favorite_id},
                )
                == 4
            )
    finally:
        async with sessions() as session, session.begin():
            await session.execute(
                text("DELETE FROM user_account WHERE public_id=:user"), {"user": user}
            )
        await engine.dispose()
