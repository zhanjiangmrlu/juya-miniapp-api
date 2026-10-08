import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import bindparam, text

from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory
from juya_miniapp_api.modules.accounts.repository import SQLAlchemyAccountRepository
from juya_miniapp_api.modules.contacts.repository import SQLAlchemyContactRepository
from juya_miniapp_api.modules.favorites.repository import SQLAlchemyFavoriteRepository
from juya_miniapp_api.modules.favorites.router import create_favorites_router
from juya_miniapp_api.modules.favorites.service import FavoriteService
from juya_miniapp_api.modules.learning.access_service import SQLAlchemyOpenHistory
from juya_miniapp_api.modules.learning.domain import ReadingPosition
from juya_miniapp_api.modules.learning.repository import SQLAlchemyLearningRepository
from juya_miniapp_api.shared.ids import new_ulid


@pytest.mark.asyncio
async def test_transaction_events_are_idempotent_paginated_and_survive_anonymized_deletion() -> (
    None
):
    # 功能:验证事务统计事件幂等可分页且匿名化注销后仍保留
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    url = os.getenv("JUYA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("isolated MySQL required")
    engine = create_engine(url.replace("mysql+pymysql://", "mysql+asyncmy://", 1))
    factory = create_session_factory(engine)
    now = datetime(2037, 1, 2, 4, tzinfo=UTC)
    public = new_ulid(now)
    event_ids = []
    internal = None
    request = None
    try:
        async with factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO user_account(public_id,juya_number,status) "
                    "VALUES(:id,:number,'ACTIVE')"
                ),
                {"id": public, "number": public[-12:]},
            )
            internal = await session.scalar(text("SELECT LAST_INSERT_ID()"))
        learning = SQLAlchemyLearningRepository(factory)
        await learning.save_progress(public, "v13-scene", 1, ReadingPosition("one"), now)
        results = await asyncio.gather(
            *(learning.complete(public, "v13-scene", f"done-{i}", now) for i in range(10))
        )
        assert sum(item.created for item in results) == 1
        await learning.save_progress(
            public, "v13-scene", 2, ReadingPosition("two"), now + timedelta(days=1)
        )
        history = SQLAlchemyOpenHistory(learning)
        await history.record_open(public, "v13-scene", now + timedelta(days=1))
        await history.record_open(public, "v13-scene", now + timedelta(days=1))
        contacts = SQLAlchemyContactRepository(factory)
        assert await contacts.record_prompt_exposure(public, "one", now)
        assert not await contacts.record_prompt_exposure(public, "one", now)
        favorites = SQLAlchemyFavoriteRepository(factory)
        service = FavoriteService(favorites)
        for i in range(125):
            await service.favorite(
                public,
                "VOCABULARY",
                f"word {i}",
                f"entry-{i}",
                "v13-scene",
                "A sentence",
                "one",
                now,
                revision_id="rev-one",
                entry_version=1,
                entry_snapshot={"english": f"word {i}", "audio_version_id": None},
            )
        pinned = await service.favorite(
            public,
            "VOCABULARY",
            "word 124",
            "entry-124",
            "v13-scene",
            "New sentence",
            "one",
            now,
            revision_id="rev-two",
            entry_version=2,
        )
        assert len(pinned.sources) == 2

        async def user() -> str:
            # 功能:提供路由测试的当前用户公开标识
            # 参数:
            #     无形参。
            # 返回:测试使用的用户公开标识
            return public

        app = FastAPI()
        app.include_router(create_favorites_router(service, favorites, user_dependency=user))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            cursor = None
            ids = []
            while True:
                response = await client.get(
                    "/api/v1/reviews/queue",
                    params={"limit": 50, **({"cursor": cursor} if cursor else {})},
                )
                assert response.status_code == 200
                payload = response.json()
                ids.extend(item["id"] for item in payload["items"])
                if not payload["has_more"]:
                    break
                cursor = payload["next_cursor"]
        assert len(ids) == 125 and len(set(ids)) == 125
        async with factory() as session:
            rows = (
                await session.execute(
                    text("SELECT id,event_type FROM analytics_event WHERE user_id=:user"),
                    {"user": internal},
                )
            ).all()
            event_ids = [row.id for row in rows]
            types = [row.event_type for row in rows]
        assert types.count("SCENE_COMPLETED") == 1
        assert types.count("USER_ACTIVE") == 2
        assert types.count("USER_ACTIVE_WEEK") == 1
        assert types.count("USER_ACTIVE_MONTH") == 1
        assert types.count("SCENE_REVISITED") == 1
        assert types.count("CONTACT_PROMPT_EXPOSED") == 1
        assert types.count("FAVORITE_CREATED") == 125
        accounts = SQLAlchemyAccountRepository(factory)
        request = await accounts.request_deletion(
            public, now - timedelta(days=8), now - timedelta(days=1)
        )
        await accounts.begin_due_deletions(now)
        await accounts.record_cross_domain_cleanup(public, request.id, succeeded=True, now=now)
        async with factory() as session:
            retained = (
                await session.execute(
                    text(
                        "SELECT user_id,event_key,event_type,occurred_at FROM analytics_event "
                        "WHERE id IN :ids"
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"ids": event_ids},
                )
            ).all()
        assert all(row.user_id is None for row in retained)
        assert all(row.event_key.startswith("anonymous:") for row in retained)
        assert len([row for row in retained if row.event_type == "USER_ACTIVE_WEEK"]) == 1
        assert all(row.occurred_at is not None for row in retained)
    finally:
        async with factory() as session, session.begin():
            if internal is not None:
                await session.execute(
                    text("DELETE FROM analytics_event WHERE user_id=:user"), {"user": internal}
                )
                for event_id in event_ids:
                    await session.execute(
                        text("DELETE FROM analytics_event WHERE id=:id"), {"id": event_id}
                    )
                await session.execute(
                    text("DELETE FROM account_deletion_request WHERE user_id=:user"),
                    {"user": internal},
                )
                await session.execute(
                    text("DELETE FROM user_account WHERE id=:user"), {"user": internal}
                )
            if request is not None:
                await session.execute(
                    text("DELETE FROM miniapp_outbox WHERE aggregate_id=:id"), {"id": request.id}
                )
        await engine.dispose()
