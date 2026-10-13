"""隔离MySQL上的场景最终写守卫和真实账号锁竞争,绝不读取默认数据库配置。"""

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from juya_miniapp_api.infrastructure.db.session import create_engine, create_session_factory
from juya_miniapp_api.integrations.admin_api.schemas import AccessProjection
from juya_miniapp_api.modules.accounts.repository import SQLAlchemyAccountRepository
from juya_miniapp_api.modules.learning.domain import ReadingPosition
from juya_miniapp_api.modules.learning.repository import SQLAlchemyLearningRepository
from juya_miniapp_api.modules.learning.service import LearningService
from juya_miniapp_api.shared.errors import AppError
from juya_miniapp_api.shared.ids import new_ulid


@pytest_asyncio.fixture
async def guarded_scene(learning_scene):
    # 功能:仅连接显式隔离库,准备当前发布和有效正式授权并清理本用例账号。
    url = os.getenv("JUYA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("isolated MySQL required")
    engine = create_engine(url.replace("mysql+pymysql://", "mysql+asyncmy://", 1))
    sessions = create_session_factory(engine)
    now = datetime.now(UTC)
    user, scene = new_ulid(now), new_ulid(now)
    async with sessions() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO user_account(public_id,juya_number,status) "
                "VALUES(:id,:number,'ACTIVE')"
            ),
            {"id": user, "number": user[-12:]},
        )
        owner = await session.scalar(text("SELECT LAST_INSERT_ID()"))
    try:
        facts = await learning_scene.create(sessions, owner, scene, now)
        yield engine, sessions, user, owner, scene, facts, now
    finally:
        await learning_scene.clear()
        async with sessions() as session, session.begin():
            await session.execute(
                text("DELETE FROM analytics_event WHERE user_id=:user"), {"user": owner}
            )
            await session.execute(text("DELETE FROM user_account WHERE id=:user"), {"user": owner})
        await engine.dispose()


async def assert_no_writes(sessions, owner):
    # 功能:验证拒权后进度/完成/打卡/统计全部没有产生新数据。
    async with sessions() as session:
        for table in (
            "learning_progress",
            "learning_completion_event",
            "daily_checkin",
            "analytics_event",
        ):
            assert (
                await session.scalar(
                    text(f"SELECT COUNT(*) FROM {table} WHERE user_id=:user"), {"user": owner}
                )
                == 0
            )


async def run_command(service, operation, user, scene, now):
    # 功能:通过真实学习服务调用进度或完成命令。
    if operation == "save":
        return await service.save_progress(user, scene, 1, ReadingPosition("sentence", 0), now)
    return await service.complete(user, scene, "complete-once", now)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["save", "complete"])
@pytest.mark.parametrize(
    "change",
    ["deleted", "revoked", "paused", "expired", "closed", "offline", "revision_unpublished"],
)
async def test_remote_preflight_cannot_authorize_a_write_after_sql_facts_change(
    guarded_scene, operation, change
):
    # 功能:远程投影成功后先提交事实变化,本地写事务必须独立拒权并保持零副作用。
    _, sessions, user, owner, scene, facts, now = guarded_scene

    class Preflight:
        async def access(self, user_id, scenes):
            # 功能:先真实提交变化,再返回旧的远程成功投影以复现竞争窗口。
            async with sessions() as session, session.begin():
                if change == "deleted":
                    await session.execute(
                        text("SELECT id FROM user_account WHERE id=:user FOR UPDATE"),
                        {"user": owner},
                    )
                    await SQLAlchemyAccountRepository._delete_learning_data(session, owner)
                    await session.execute(
                        text("UPDATE user_account SET status='DELETED' WHERE id=:user"),
                        {"user": owner},
                    )
                elif change in {"revoked", "paused"}:
                    await session.execute(
                        text("UPDATE formal_entitlement SET status=:status WHERE id=:grant"),
                        {"status": change.upper(), "grant": facts["grant"]},
                    )
                elif change == "expired":
                    await session.execute(
                        text(
                            "UPDATE formal_entitlement SET term='month_1',expires_at=:expiry "
                            "WHERE id=:grant"
                        ),
                        {
                            "expiry": (now - timedelta(seconds=1)).replace(tzinfo=None),
                            "grant": facts["grant"],
                        },
                    )
                elif change == "closed":
                    await session.execute(
                        text(
                            "UPDATE learning_module_config SET enabled=0 "
                            "WHERE module_type='scene_learning'"
                        )
                    )
                elif change == "offline":
                    await session.execute(
                        text("UPDATE scene SET status='OFFLINE' WHERE id=:scene"), facts
                    )
                else:
                    await session.execute(
                        text("UPDATE scene_revision SET status='SUPERSEDED' WHERE id=:revision"),
                        facts,
                    )
            return [AccessProjection(scene_id=scene, level="FORMAL")]

    service = LearningService(SQLAlchemyLearningRepository(sessions), Preflight())
    with pytest.raises(AppError) as denied:
        await run_command(service, operation, user, scene, now)
    assert denied.value.code == (
        "ACCOUNT_NOT_ACTIVE" if change == "deleted" else "SCENE_ACCESS_DENIED"
    )
    assert denied.value.status_code == 403
    await assert_no_writes(sessions, owner)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["save", "complete"])
async def test_account_cleanup_committed_while_scene_waits_for_account_lock_is_not_rewritten(
    guarded_scene, operation
):
    # 功能:真实MySQL账号锁阻塞在途写请求,清理并注销提交后旧请求拒绝重写。
    engine, sessions, user, owner, scene, _, now = guarded_scene
    waiting = asyncio.Event()

    class ObservedSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            # 功能:标记旧请求已尝试取得真实账号锁,不替换SQL执行。
            if str(statement).startswith("SELECT id,status FROM user_account"):
                waiting.set()
            return await super().execute(statement, *args, **kwargs)

    guarded_sessions = async_sessionmaker(engine, class_=ObservedSession, expire_on_commit=False)
    service = LearningService(SQLAlchemyLearningRepository(guarded_sessions))
    task = None
    try:
        async with sessions() as deleting, deleting.begin():
            await deleting.execute(
                text("SELECT id FROM user_account WHERE id=:user FOR UPDATE"), {"user": owner}
            )
            task = asyncio.create_task(run_command(service, operation, user, scene, now))
            await asyncio.wait_for(waiting.wait(), timeout=5)
            await SQLAlchemyAccountRepository._delete_learning_data(deleting, owner)
            await deleting.execute(
                text("UPDATE user_account SET status='DELETED' WHERE id=:user"), {"user": owner}
            )
        with pytest.raises(AppError) as denied:
            await asyncio.wait_for(task, timeout=5)
        assert denied.value.code == "ACCOUNT_NOT_ACTIVE"
        await assert_no_writes(sessions, owner)
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["save", "complete"])
@pytest.mark.parametrize("change", ["module", "entitlement", "publication"])
async def test_fact_lock_reads_committed_change_instead_of_initial_repeatable_read_snapshot(
    guarded_scene, operation, change
):
    # 功能:发布资料的初始快照旧于并发关闭/撤权提交时,锁定事实读取最新状态并拒绝写入。
    engine, sessions, user, owner, scene, facts, now = guarded_scene
    waiting = asyncio.Event()

    class ObservedSession(AsyncSession):
        async def scalar(self, statement, *args, **kwargs):
            # 功能:标记请求已尝试读取模块锁,保留真实MySQL阻塞行为。
            if change == "module" and "SELECT enabled FROM learning_module_config" in str(
                statement
            ):
                waiting.set()
            return await super().scalar(statement, *args, **kwargs)

        async def execute(self, statement, *args, **kwargs):
            # 功能:标记请求已尝试读取权益或发布锁,保留真实MySQL阻塞行为。
            query = str(statement)
            if (
                change == "entitlement"
                and query.startswith("SELECT public_id,status,version,expires_at")
            ) or (
                change == "publication"
                and query.startswith("SELECT id,status,published_revision_id FROM scene")
            ):
                waiting.set()
            return await super().execute(statement, *args, **kwargs)

    guarded_sessions = async_sessionmaker(engine, class_=ObservedSession, expire_on_commit=False)
    service = LearningService(SQLAlchemyLearningRepository(guarded_sessions))
    task = None
    try:
        async with sessions() as mutating, mutating.begin():
            if change == "module":
                await mutating.execute(
                    text(
                        "UPDATE learning_module_config SET enabled=0 "
                        "WHERE module_type='scene_learning'"
                    )
                )
            elif change == "entitlement":
                await mutating.execute(
                    text("UPDATE formal_entitlement SET status='REVOKED' WHERE id=:grant"), facts
                )
            else:
                await mutating.execute(
                    text("UPDATE scene SET status='OFFLINE' WHERE id=:scene"), facts
                )
            task = asyncio.create_task(run_command(service, operation, user, scene, now))
            await asyncio.wait_for(waiting.wait(), timeout=5)
        with pytest.raises(AppError) as denied:
            await asyncio.wait_for(task, timeout=5)
        assert denied.value.code == "SCENE_ACCESS_DENIED"
        await assert_no_writes(sessions, owner)
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_saved_completion_cannot_be_replayed_after_revocation(guarded_scene):
    # 功能:已完成的幂等请求也必须受当前授权约束,拒权不新增统计/打卡。
    _, sessions, user, owner, scene, facts, now = guarded_scene
    service = LearningService(SQLAlchemyLearningRepository(sessions))
    first = await run_command(service, "complete", user, scene, now)
    second = await run_command(service, "complete", user, scene, now)
    assert first.created and not second.created
    async with sessions() as session, session.begin():
        await session.execute(
            text("UPDATE formal_entitlement SET status='REVOKED' WHERE id=:grant"), facts
        )
    with pytest.raises(AppError) as denied:
        await run_command(service, "complete", user, scene, now)
    assert denied.value.code == "SCENE_ACCESS_DENIED"
    async with sessions() as session:
        for table in ("learning_progress", "learning_completion_event", "daily_checkin"):
            assert (
                await session.scalar(
                    text(f"SELECT COUNT(*) FROM {table} WHERE user_id=:user"), {"user": owner}
                )
                == 1
            )
        assert (
            await session.scalar(
                text(
                    "SELECT COUNT(*) FROM analytics_event WHERE user_id=:user "
                    "AND event_type='SCENE_COMPLETED'"
                ),
                {"user": owner},
            )
            == 1
        )
