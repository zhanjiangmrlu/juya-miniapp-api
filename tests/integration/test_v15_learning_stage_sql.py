"""Actual private MySQL projection; presentation stage never confers access."""

import os
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text

from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory
from juya_miniapp_api.modules.accounts.repository import SQLAlchemyAccountRepository
from juya_miniapp_api.modules.learning.stage import SQLLearningStageReader
from juya_miniapp_api.shared.errors import AppError
from juya_miniapp_api.shared.ids import new_ulid


@pytest_asyncio.fixture
async def stage_context():
    url = os.getenv("JUYA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("isolated MySQL required")
    engine = create_engine(url.replace("mysql+pymysql://", "mysql+asyncmy://", 1))
    sessions = create_session_factory(engine)
    now = datetime.now(UTC)
    user = new_ulid(now)
    try:
        async with sessions() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO user_account(public_id,juya_number,status) "
                    "VALUES(:id,:number,'ACTIVE')"
                ),
                {"id": user, "number": "JY" + user[-12:]},
            )
            owner = await session.scalar(text("SELECT LAST_INSERT_ID()"))
        yield sessions, SQLLearningStageReader(sessions), user, owner, now
    finally:
        # Only this fixture's generated actor/series; do not leave a new global open set behind.
        async with sessions() as session, session.begin():
            await session.execute(
                text(
                    "DELETE i FROM open_scene_item i JOIN open_scene_config c ON c.id=i.config_id "
                    "WHERE c.actor_public_id=:actor"
                ),
                {"actor": user},
            )
            await session.execute(
                text("DELETE FROM open_scene_config WHERE actor_public_id=:actor"), {"actor": user}
            )
            await session.execute(
                text(
                    "DELETE s FROM scene s JOIN content_series c ON c.id=s.series_id "
                    "WHERE c.public_id=:series"
                ),
                {"series": user},
            )
            await session.execute(
                text("DELETE FROM content_series WHERE public_id=:series"), {"series": user}
            )
        await engine.dispose()


async def formal(sessions, owner, now, status, term, expiry):
    async with sessions() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO content_package(public_id,name,status) "
                "VALUES(:id,'private stage fixture','ACTIVE')"
            ),
            {"id": new_ulid(now)},
        )
        package = await session.scalar(text("SELECT LAST_INSERT_ID()"))
        await session.execute(
            text(
                "INSERT INTO formal_entitlement(public_id,user_id,package_id,term,status,"
                "granted_at,expires_at,updated_at) VALUES(:id,:user,:package,:term,:status,"
                ":now,:expires,:now)"
            ),
            {
                "id": new_ulid(now),
                "user": owner,
                "package": package,
                "term": term,
                "status": status,
                "now": now.replace(tzinfo=None),
                "expires": expiry,
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,term,offset,expected",
    [
        ("ACTIVE", "permanent", None, "ENTITLED"),
        ("ACTIVE", "month_1", 1, "ENTITLED"),
        ("ACTIVE", "month_1", 0, "NEW"),
        ("ACTIVE", "month_1", -1, "NEW"),
        ("PAUSED", "permanent", None, "NEW"),
        ("REVOKED", "permanent", None, "NEW"),
    ],
)
async def test_stage_formal_current_status_and_exact_expiry_boundary(
    stage_context, status, term, offset, expected
):
    sessions, reader, user, owner, now = stage_context
    expiry = (now + timedelta(seconds=offset)).replace(tzinfo=None) if offset is not None else None
    await formal(sessions, owner, now, status, term, expiry)
    facts = await reader.get(user, now)
    assert facts.stage == expected
    assert facts.has_valid_entitlement == (expected == "ENTITLED")
    assert not facts.has_started_non_open


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,offset,expected",
    [
        ("PENDING", 1, "ENTITLED"),
        ("PENDING", 0, "NEW"),
        ("PENDING", -1, "NEW"),
        ("ACTIVE", 1, "ENTITLED"),
        ("ACTIVE", 0, "NEW"),
        ("ACTIVE", -1, "NEW"),
        ("PAUSED", 1, "NEW"),
        ("REVOKED", 1, "NEW"),
        ("ENDED", 1, "NEW"),
    ],
)
async def test_stage_limited_pending_deadline_and_active_expiry(
    stage_context, status, offset, expected
):
    sessions, reader, user, owner, now = stage_context
    async with sessions() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO limited_campaign(public_id,name,status) "
                "VALUES(:id,'private stage fixture','OPEN')"
            ),
            {"id": new_ulid(now)},
        )
        campaign = await session.scalar(text("SELECT LAST_INSERT_ID()"))
        await session.execute(
            text(
                "INSERT INTO limited_campaign_version(public_id,campaign_id,version_no,status,"
                "duration_days,activation_window_days,capacity) "
                "VALUES(:id,:campaign,1,'OPEN',3,5,1)"
            ),
            {"id": new_ulid(now), "campaign": campaign},
        )
        version = await session.scalar(text("SELECT LAST_INSERT_ID()"))
        boundary = (now + timedelta(seconds=offset)).replace(tzinfo=None)
        await session.execute(
            text(
                "INSERT INTO limited_entitlement(public_id,user_id,campaign_version_id,status,"
                "granted_at,start_deadline,activated_at,expires_at,updated_at) "
                "VALUES(:id,:user,:version,:status,:now,:deadline,:activated,:expires,:now)"
            ),
            {
                "id": new_ulid(now),
                "user": owner,
                "version": version,
                "status": status,
                "now": now.replace(tzinfo=None),
                "deadline": boundary,
                "activated": None if status == "PENDING" else now.replace(tzinfo=None),
                "expires": None if status == "PENDING" else boundary,
            },
        )
    assert (await reader.get(user, now)).stage == expected


@pytest.mark.asyncio
async def test_stage_account_ownership_and_profile_contact_have_no_authorizing_effect(
    stage_context,
):
    sessions, reader, user, owner, now = stage_context
    assert (await reader.get(user, now)).stage == "NEW"
    stranger = new_ulid(now)
    with pytest.raises(AppError) as missing:
        await reader.get(stranger, now)
    assert missing.value.code == "ACCOUNT_NOT_ACTIVE" and missing.value.status_code == 403
    async with sessions() as session, session.begin():
        await session.execute(
            text("INSERT INTO user_profile(user_id,nickname) VALUES(:user,'private fixture')"),
            {"user": owner},
        )
        await session.execute(
            text("INSERT INTO user_contact(user_id,contact_status) VALUES(:user,'CONTACTED')"),
            {"user": owner},
        )
        await session.execute(
            text(
                "INSERT INTO user_account(public_id,juya_number,status) "
                "VALUES(:id,:number,'ACTIVE')"
            ),
            {"id": stranger, "number": "JY" + stranger[-12:]},
        )
        other = await session.scalar(text("SELECT LAST_INSERT_ID()"))
    await formal(sessions, other, now, "ACTIVE", "permanent", None)
    assert (await reader.get(stranger, now)).stage == "ENTITLED"
    assert (await reader.get(user, now)).stage == "NEW"
    async with sessions() as session, session.begin():
        await session.execute(
            text("UPDATE user_account SET status='DELETED' WHERE id=:id"), {"id": owner}
        )
    with pytest.raises(AppError) as inactive:
        await reader.get(user, now)
    assert inactive.value.status_code == 403


@pytest.mark.asyncio
async def test_stage_uses_open_configuration_effective_at_original_start_and_keeps_non_open_history(
    stage_context,
):
    sessions, reader, user, owner, now = stage_context
    opened, private = [new_ulid(now) for _ in range(2)]
    series = user
    started = now - timedelta(hours=2)
    async with sessions() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO content_series(public_id,slug,title,status) "
                "VALUES(:id,:id,'private stage fixture','PUBLISHED')"
            ),
            {"id": series},
        )
        series_id = await session.scalar(text("SELECT LAST_INSERT_ID()"))
        number = await session.scalar(
            text(
                "SELECT COALESCE(MAX(version),0)+1 "
                "FROM content_template WHERE template_type='dialogue'"
            )
        )
        await session.execute(
            text(
                "INSERT INTO content_template(template_type,version,"
                "required_modules,validation_rules) "
                "VALUES('dialogue',:version,'[]','{}')"
            ),
            {"version": number},
        )
        template = await session.scalar(text("SELECT LAST_INSERT_ID()"))
        scene_ids = []
        for identity in (opened, private):
            await session.execute(
                text(
                    "INSERT INTO scene(public_id,series_id,template_id,title,"
                    "status) VALUES(:id,:series,:template,'stage','PUBLISHED')"
                ),
                {"id": identity, "series": series_id, "template": template},
            )
            scene_ids.append(await session.scalar(text("SELECT LAST_INSERT_ID()")))
        config_version = await session.scalar(
            text("SELECT COALESCE(MAX(version),0)+1 FROM open_scene_config")
        )
        await session.execute(
            text(
                "INSERT INTO open_scene_config(version,activated_at,"
                "actor_public_id) VALUES(:version,:at,:actor)"
            ),
            {
                "version": config_version,
                "at": (started - timedelta(hours=1)).replace(tzinfo=None),
                "actor": user,
            },
        )
        original = await session.scalar(text("SELECT LAST_INSERT_ID()"))
        await session.execute(
            text(
                "INSERT INTO open_scene_item(config_id,scene_id,position) VALUES(:config,:scene,1)"
            ),
            {"config": original, "scene": scene_ids[0]},
        )
        await session.execute(
            text(
                "INSERT INTO learning_progress(user_id,scene_id,source_type,"
                "position,started_at,last_learned_at) "
                "VALUES(:user,:scene,'SCENE','{}',:at,:at)"
            ),
            {"user": owner, "scene": opened, "at": started.replace(tzinfo=None)},
        )
    assert (await reader.get(user, now)).stage == "NEW"
    async with sessions() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO open_scene_config(version,activated_at,"
                "actor_public_id) VALUES(:version,:at,:actor)"
            ),
            {
                "version": config_version + 1,
                "at": (started + timedelta(hours=1)).replace(tzinfo=None),
                "actor": user,
            },
        )
        changed = await session.scalar(text("SELECT LAST_INSERT_ID()"))
        await session.execute(
            text(
                "INSERT INTO open_scene_item(config_id,scene_id,position) VALUES(:config,:scene,1)"
            ),
            {"config": changed, "scene": scene_ids[1]},
        )
    # An originally open start does not become private after the open set changes.
    assert (await reader.get(user, now)).stage == "NEW"
    await formal(sessions, owner, now, "REVOKED", "month_1", started.replace(tzinfo=None))
    async with sessions() as session, session.begin():
        # This was non-open at started_at, though it is in the later open configuration.
        await session.execute(
            text(
                "INSERT INTO learning_progress(user_id,scene_id,source_type,"
                "position,started_at,last_learned_at) "
                "VALUES(:user,:scene,'SCENE','{}',:at,:at)"
            ),
            {"user": owner, "scene": private, "at": started.replace(tzinfo=None)},
        )
        await session.execute(
            text("UPDATE scene SET status='OFFLINE' WHERE public_id=:id"), {"id": private}
        )
    facts = await reader.get(user, now)
    assert facts.stage == "ENTITLED" and facts.has_started_non_open
    assert not facts.has_valid_entitlement
    from juya_miniapp_api.integrations.admin_api.v15_facts import SQLAuthorizer

    async with sessions() as session:
        access = await SQLAuthorizer()(session, user, "dialogue", private, now, lock=False)
    assert not access.full_access
    # Hiding currently accessible rows/modules cannot erase the presentation history.
    async with sessions() as session:
        modules = (
            (await session.execute(text("SELECT module_type,enabled FROM learning_module_config")))
            .mappings()
            .all()
        )
    try:
        async with sessions() as session, session.begin():
            await session.execute(text("UPDATE learning_module_config SET enabled=0"))
        assert (await reader.get(user, now)).stage == "ENTITLED"
    finally:
        async with sessions() as session, session.begin():
            for module in modules:
                await session.execute(
                    text(
                        "UPDATE learning_module_config SET enabled=:enabled "
                        "WHERE module_type=:module_type"
                    ),
                    dict(module),
                )
    await SQLAlchemyAccountRepository(sessions).clear_learning_data(user)
    assert (await reader.get(user, now)).stage == "NEW"


@pytest.mark.asyncio
async def test_stage_short_reading_history_is_independent_read_only_and_clears(stage_context):
    sessions, reader, user, owner, now = stage_context
    async with sessions() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO short_reading_progress(user_id,short_reading_id,"
                "saved_content_version,paragraph_id,offset,position,"
                "last_client_sequence,started_at,last_read_at,completed_at) "
                "VALUES(:user,:reading,1,NULL,0,'{}',0,:at,:at,NULL)"
            ),
            {"user": owner, "reading": new_ulid(now), "at": now.replace(tzinfo=None)},
        )
    for _ in range(3):
        facts = await reader.get(user, now)
        assert facts.stage == "ENTITLED" and facts.has_started_non_open
        assert not facts.has_valid_entitlement
    async with sessions() as session:
        for table in (
            "learning_progress",
            "learning_completion_event",
            "daily_checkin",
            "user_contact",
            "formal_entitlement",
            "limited_entitlement",
        ):
            assert (
                await session.scalar(
                    text(f"SELECT COUNT(*) FROM {table} WHERE user_id=:user"), {"user": owner}
                )
                == 0
            )
        assert (
            await session.scalar(
                text("SELECT completed_at FROM short_reading_progress WHERE user_id=:user"),
                {"user": owner},
            )
            is None
        )
    await SQLAlchemyAccountRepository(sessions).clear_learning_data(user)
    assert (await reader.get(user, now)).stage == "NEW"
