import asyncio
import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory
from juya_miniapp_api.infrastructure.security.field_cipher import FieldCipher
from juya_miniapp_api.modules.contacts.repository import SQLAlchemyContactRepository
from juya_miniapp_api.modules.contacts.service import ContactService
from juya_miniapp_api.modules.favorites.repository import SQLAlchemyFavoriteRepository
from juya_miniapp_api.modules.favorites.service import FavoriteService
from juya_miniapp_api.shared.errors import AppError
from juya_miniapp_api.shared.ids import new_ulid


@pytest.mark.asyncio
async def test_sql_reviews_validate_snapshot_and_contacts_emit_one_conversion():
    # 功能:验证SQL复习校验固定快照且联系方式只产生一次转化事件
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    url = os.getenv("JUYA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("isolated MySQL required")
    engine = create_engine(url.replace("mysql+pymysql://", "mysql+asyncmy://"))
    factory = create_session_factory(engine)
    now = datetime(2037, 1, 2, 4, tzinfo=UTC)
    users = [new_ulid(now), new_ulid(now)]
    internal = []
    try:
        async with factory() as session, session.begin():
            for public in users:
                await session.execute(
                    text(
                        "INSERT INTO user_account(public_id,juya_number,status) "
                        "VALUES(:id,:number,'ACTIVE')"
                    ),
                    {"id": public, "number": public[-12:]},
                )
                internal.append(await session.scalar(text("SELECT LAST_INSERT_ID()")))
        repo = SQLAlchemyFavoriteRepository(factory)
        favorites = FavoriteService(repo)
        cards = [
            await favorites.favorite(
                users[0], "VOCABULARY", word, word, "scene", "sentence", "one", now
            )
            for word in ("one", "two")
        ]
        foreign = await favorites.favorite(
            users[1], "VOCABULARY", "foreign", "foreign", "scene", "sentence", "one", now
        )
        for invalid in ([], ["missing"], [foreign.public_id], [cards[0].public_id] * 2):
            with pytest.raises(AppError):
                await favorites.create_review(users[0], invalid, new_ulid(now), now)
        review = await favorites.create_review(users[0], [cards[0].public_id], "b-review", now)
        with pytest.raises(AppError):
            await favorites.create_review(users[0], [cards[1].public_id], "b-review", now)
        outcomes = await asyncio.gather(
            *(
                favorites.complete_review(users[0], review.id, f"done-{index}", now)
                for index in range(10)
            )
        )
        assert sum(item.created for item in outcomes) == 1
        assert (await repo.get(users[0], cards[0].public_id)).last_reviewed_at == now
        assert (await repo.get(users[0], cards[1].public_id)).last_reviewed_at is None
        earlier = await favorites.create_review(users[0], [cards[0].public_id], "b-earlier", now)
        later = await favorites.create_review(users[0], [cards[0].public_id], "b-later", now)
        await favorites.complete_review(users[0], later.id, "later", now + timedelta(minutes=3))
        await favorites.complete_review(users[0], earlier.id, "earlier", now + timedelta(minutes=1))
        assert (await repo.get(users[0], cards[0].public_id)).last_reviewed_at == now + timedelta(
            minutes=3
        )
        await favorites.complete_review(users[0], review.id, "next-day", now + timedelta(days=1))
        deleted_review = await favorites.create_review(
            users[0], [cards[1].public_id], "b-deleted", now
        )
        await favorites.delete(users[0], cards[1].public_id)
        with pytest.raises(AppError):
            await favorites.complete_review(users[0], deleted_review.id, "invalid", now)
        contact = ContactService(
            SQLAlchemyContactRepository(factory), FieldCipher(b"k" * 32, b"h" * 32)
        )
        assert await contact.record_prompt_exposure(users[0], "b-exposure", now)
        assert not await contact.record_prompt_exposure(users[0], "b-exposure", now)
        await contact.save(users[0], "fixture_one", "v1", "TEST", now)
        await contact.save(users[0], "fixture_two", "v1", "TEST", now + timedelta(minutes=1))
        async with factory() as session:
            events = (
                await session.execute(
                    text(
                        "SELECT event_type,payload FROM analytics_event "
                        "WHERE user_id=:user ORDER BY occurred_at,id"
                    ),
                    {"user": internal[0]},
                )
            ).all()
            types = [event.event_type for event in events]
            assert types.count("REVIEW_COMPLETED") == 3
            assert types.count("CONTACT_SUBMITTED") == types.count("CONTACT_CHANGED") == 1
            assert (
                await session.scalar(
                    text("SELECT COUNT(*) FROM daily_checkin WHERE user_id=:user"),
                    {"user": internal[0]},
                )
                == 1
            )
            contact_events = [
                event
                for event in events
                if event.event_type in {"CONTACT_PROMPT_EXPOSED", "CONTACT_SUBMITTED"}
            ]
            tokens = [
                (json.loads(event.payload) if isinstance(event.payload, str) else event.payload)[
                    "contact_cohort"
                ]
                for event in contact_events
            ]
            assert len(set(tokens)) == 1
    finally:
        await engine.dispose()
