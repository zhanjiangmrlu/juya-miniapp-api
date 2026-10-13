"""真实场景服务/SQL仓库/授权守卫的确定性交错;SQL会话替身不连接外部数据库。"""

import json
from datetime import UTC, datetime, timedelta

import pytest

from juya_miniapp_api.integrations.admin_api.schemas import AccessProjection
from juya_miniapp_api.modules.learning.access_service import SQLAlchemyOpenHistory
from juya_miniapp_api.modules.learning.domain import ReadingPosition
from juya_miniapp_api.modules.learning.repository import SQLAlchemyLearningRepository
from juya_miniapp_api.modules.learning.service import LearningService
from juya_miniapp_api.shared.errors import AppError


class Rows:
    def __init__(self, rows=(), rowcount=0):
        # 功能:构造SQL查询结果替身。
        self.rows = list(rows)
        self.rowcount = rowcount

    def mappings(self):
        # 功能:提供真实仓库消费的映射查询入口。
        return self

    def first(self):
        # 功能:返回模拟查询的首行或空值。
        return self.rows[0] if self.rows else None

    def all(self):
        # 功能:返回模拟查询的全部行。
        return self.rows


class SceneSession:
    """替换外部SQL执行,同时保留生产服务、事务入口及所有最终授权分支。"""

    def __init__(self):
        # 功能:初始化受控账号/发布/权益资料及写入结果。
        self.now = datetime.now(UTC)
        self.status = "ACTIVE"
        self.enabled = True
        self.published = True
        self.revision_published = True
        self.entitlement = "ACTIVE"
        self.expires_at = None
        self.kind = "dialogue"
        self.changed_kind = None
        self.progress = None
        self.writes = []
        self.events = {}
        self.completions = []
        self.checkins = set()
        self.in_transaction = False

    def begin(self):
        # 功能:提供受控事务上下文。
        return self

    async def __aenter__(self):
        # 功能:进入受控会话和事务。
        self.in_transaction = True
        return self

    async def __aexit__(self, *args):
        # 功能:结束受控事务。
        self.in_transaction = False

    async def scalar(self, statement, params=None):
        # 功能:执行守卫与统计依赖的受控SQL标量查询。
        query = str(statement)
        if "SELECT id FROM user_account" in query:
            return 7
        if "SELECT enabled FROM learning_module_config" in query:
            return self.enabled
        if "SELECT version FROM discovery_config_state" in query:
            return 1
        if "SELECT MAX(id) FROM open_scene_config" in query:
            return None
        if "FROM open_scene_item" in query:
            return None
        if "FROM preview_config" in query:
            return False
        raise AssertionError(f"Unexpected scalar: {query}")

    async def execute(self, statement, params=None):
        # 功能:替换外部SQL边界,保留真实仓库的读取及写入效果。
        query = str(statement)
        params = params or {}
        if query.startswith("SELECT id,status") and "FROM user_account" in query:
            return Rows([{"id": 7, "status": self.status, "updated_at": self.now}])
        if query.startswith("SELECT r.public_id"):
            if not self.published or not self.revision_published:
                return Rows()
            if "FOR SHARE" in query and self.changed_kind:
                self.kind = self.changed_kind
            return Rows(
                [
                    {
                        "public_id": "revision",
                        "version_no": 1,
                        "content_snapshot": {"schema_version": 2, "template_type": self.kind},
                    }
                ]
            )
        if query.startswith("SELECT id,status,published_revision_id"):
            return Rows(
                [
                    {
                        "id": 8,
                        "status": "PUBLISHED" if self.published else "OFFLINE",
                        "published_revision_id": 9,
                    }
                ]
            )
        if query.startswith("SELECT p.id,p.status"):
            return Rows([{"id": 10, "status": "ACTIVE"}])
        if query.startswith("SELECT public_id,status,version,expires_at"):
            return Rows(
                [
                    {
                        "public_id": "grant",
                        "status": self.entitlement,
                        "version": 1,
                        "expires_at": self.expires_at,
                    }
                ]
            )
        if query.startswith(("SELECT id,version FROM open_scene_config", "SELECT le.public_id")):
            return Rows()
        if query.startswith("SELECT u.public_id, p.scene_id"):
            return Rows([self.progress] if self.progress else [])
        assert self.in_transaction, "Writes must remain in the same local transaction"
        self.writes.append(query)
        if query.startswith("INSERT INTO learning_progress"):
            self.progress = {
                "public_id": "user",
                "scene_id": "scene",
                "source_type": "SCENE",
                "position": params["position"],
                "last_client_sequence": params.get("sequence", 0),
                "started_at": params["now"],
                "last_learned_at": params["now"],
                "completed_at": params["now"] if "completed_at" in query else None,
            }
        elif query.startswith("UPDATE learning_progress"):
            self.progress["last_learned_at"] = params["now"]
            if "position" in params:
                self.progress.update(
                    position=params["position"], last_client_sequence=params["sequence"]
                )
            else:
                self.progress["completed_at"] = params["now"]
        elif "INTO analytics_event" in query:
            self.events.setdefault(params["key"], params["type"])
        elif "INTO learning_completion_event" in query:
            self.completions.append(params["idempotency_key"])
        elif "INTO daily_checkin" in query:
            self.checkins.add(params["learning_day"])
        else:
            raise AssertionError(f"Unexpected write: {query}")
        return Rows(rowcount=1)


async def command(service, operation, session):
    # 功能:调用真实服务或场景打开历史入口,覆盖相同事务仓库的三个写入口。
    if operation == "save":
        return await service.save_progress(
            "user", "scene", 1, ReadingPosition("sentence", 0), session.now
        )
    if operation == "complete":
        return await service.complete("user", "scene", "complete", session.now)
    return await SQLAlchemyOpenHistory(service._repository).record_open(
        "user", "scene", session.now
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["save", "complete", "open"])
@pytest.mark.parametrize(
    "change,code",
    [
        ("deleted", "ACCOUNT_NOT_ACTIVE"),
        ("deleting", "ACCOUNT_NOT_ACTIVE"),
        ("revoked", "SCENE_ACCESS_DENIED"),
        ("paused", "SCENE_ACCESS_DENIED"),
        ("expired", "SCENE_ACCESS_DENIED"),
        ("module_closed", "SCENE_ACCESS_DENIED"),
        ("offline", "SCENE_ACCESS_DENIED"),
        ("revision_offline", "SCENE_ACCESS_DENIED"),
        ("kind_changed", "SCENE_ACCESS_DENIED"),
    ],
)
async def test_inflight_scene_write_rechecks_final_facts_before_any_side_effect(
    operation, change, code
):
    # 功能:前置鉴权成功后注销/关闭/撤权抢先提交时,真实仓库拒绝进度及完成全部副作用。
    session = SceneSession()

    class Access:
        async def access(self, user_id, scene_ids):
            # 功能:在前置投影成功返回前提交指定的权威事实变化。
            if change in {"deleted", "deleting"}:
                session.status = "DELETED" if change == "deleted" else "DELETION_PENDING"
            elif change in {"revoked", "paused"}:
                session.entitlement = change.upper()
            elif change == "expired":
                session.expires_at = session.now - timedelta(seconds=1)
            elif change == "module_closed":
                session.enabled = False
            elif change == "offline":
                session.published = False
            elif change == "revision_offline":
                session.revision_published = False
            else:
                session.changed_kind = "word_card"
            return [AccessProjection(scene_id="scene", level="FORMAL")]

    service = LearningService(SQLAlchemyLearningRepository(lambda: session), Access())
    if operation == "open":
        await service._authorize("user", "scene", session.now)
    with pytest.raises(AppError) as denied:
        await command(service, operation, session)
    assert (denied.value.code, denied.value.status_code) == (code, 403)
    assert session.progress is None
    assert session.writes == []
    assert not session.events and not session.completions and not session.checkins


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["dialogue", "word_card", "vocabulary"])
async def test_valid_progress_and_completion_remain_monotonic_and_idempotent(
    kind,
):
    # 功能:正常授权仍能保存单调进度,重复完成只产生一次完成/打卡。
    session = SceneSession()
    session.kind = kind
    service = LearningService(SQLAlchemyLearningRepository(lambda: session))
    await service.save_progress("user", "scene", 2, ReadingPosition("second", 3), session.now)
    old = await service.save_progress("user", "scene", 1, ReadingPosition("first", 0), session.now)
    assert old.client_sequence == 2 and old.position.entry_id == "second"
    assert json.loads(session.progress["position"]) == {"entry_id": "second", "offset": 3}
    first = await service.complete("user", "scene", "complete", session.now)
    second = await service.complete("user", "scene", "complete", session.now)
    assert first.created and not second.created
    assert session.completions == ["complete"] and len(session.checkins) == 1
    assert list(session.events.values()).count("SCENE_COMPLETED") == 1
    before = len(session.writes)
    session.entitlement = "REVOKED"
    with pytest.raises(AppError) as revoked:
        await service.complete("user", "scene", "complete", session.now)
    assert revoked.value.code == "SCENE_ACCESS_DENIED"
    assert len(session.writes) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["save", "complete"])
async def test_scene_expiry_while_waiting_for_published_revision_lock_blocks_all_writes(
    monkeypatch, operation
):
    # 功能:最终发布锁等待跨越有效期时,即使最初授权事实有效也不得写入。
    from juya_miniapp_api.integrations.admin_api import v15_guard

    current = [datetime.now(UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            # 功能:返回测试控制的锁等待时间。
            return current[0]

    class WaitingSession(SceneSession):
        async def execute(self, statement, params=None):
            # 功能:模拟取得最终发布锁前等待跨越有效期。
            query = str(statement)
            if query.startswith("SELECT r.public_id") and "FOR SHARE" in query:
                current[0] += timedelta(seconds=2)
            return await super().execute(statement, params)

    monkeypatch.setattr(v15_guard, "datetime", Clock)
    session = WaitingSession()
    session.expires_at = current[0] + timedelta(seconds=1)
    service = LearningService(SQLAlchemyLearningRepository(lambda: session))
    with pytest.raises(AppError) as expired:
        await command(service, operation, session)
    assert (expired.value.code, expired.value.status_code) == ("SCENE_ACCESS_DENIED", 403)
    assert session.writes == [] and session.progress is None
