import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from juya_miniapp_api.modules.accounts.repository import InMemoryAccountRepository
from juya_miniapp_api.modules.accounts.service import AccountLifecycleService
from juya_miniapp_api.shared.errors import AppError

NOW = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)


class RevocationRecorder:
    def __init__(self) -> None:
        # 功能:初始化账号注销的RevocationRecorder对象的状态存储
        # 参数:
        #     self: 当前账号注销的RevocationRecorder实例
        # 返回:无返回值。
        self.users: list[str] = []

    async def __call__(self, user_id: str, reason: str, now: datetime) -> None:
        # 功能:记录账号注销测试中的会话撤销调用
        # 参数:
        #     self: 当前账号注销的RevocationRecorder实例
        #     user_id: 当前操作所属用户的公开标识
        #     reason: 撤销、纠错或反馈异议的业务原因说明
        #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
        # 返回:无返回值。
        del reason, now
        self.users.append(user_id)


@pytest.mark.asyncio
async def test_clear_learning_data_preserves_account_contact_and_entitlements() -> None:
    # 功能:验证清空学习数据保留账号、联系方式与授权权益
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    repository = InMemoryAccountRepository()
    repository.seed_user("user-1")
    repository.learning_rows.add("user-1")
    repository.favorite_rows.add("user-1")
    repository.review_rows.add("user-1")
    repository.checkin_rows.add("user-1")
    repository.contacts.add("user-1")
    repository.entitlements.add("user-1")
    service = AccountLifecycleService(repository, RevocationRecorder())

    await service.clear_learning_data("user-1", "CLEAR_LEARNING_DATA")

    assert "user-1" not in repository.learning_rows
    assert "user-1" not in repository.favorite_rows
    assert "user-1" not in repository.review_rows
    assert "user-1" not in repository.checkin_rows
    assert "user-1" in repository.contacts
    assert "user-1" in repository.entitlements

    with pytest.raises(AppError) as confirmation:
        await service.clear_learning_data("user-1", "yes")
    assert confirmation.value.code == "CONFIRMATION_REQUIRED"


@pytest.mark.asyncio
async def test_deletion_request_is_idempotent_and_effective_after_seven_days() -> None:
    # 功能:验证注销申请幂等且七天等待期后生效
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    repository = InMemoryAccountRepository()
    repository.seed_user("user-1")
    service = AccountLifecycleService(repository, RevocationRecorder())

    first = await service.request_deletion("user-1", NOW)
    duplicate = await service.request_deletion("user-1", NOW + timedelta(minutes=1))

    assert duplicate.id == first.id
    assert first.status == "PENDING"
    assert first.effective_at == NOW + timedelta(days=7)
    assert repository.user_status["user-1"] == "DELETION_PENDING"


@pytest.mark.asyncio
async def test_revoke_and_due_execution_have_one_final_state() -> None:
    # 功能:验证注销撤回与到期执行竞争只保留一个最终状态
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    repository = InMemoryAccountRepository()
    repository.seed_user("user-1")
    revocations = RevocationRecorder()
    service = AccountLifecycleService(repository, revocations)
    request = await service.request_deletion("user-1", NOW - timedelta(days=7))

    results = await asyncio.gather(
        service.revoke_deletion("user-1", NOW),
        service.execute_due_deletions(NOW),
        return_exceptions=True,
    )

    current = repository.deletions[request.id]
    assert current.status in {"REVOKED", "DELETING"}
    if current.status == "REVOKED":
        assert repository.user_status["user-1"] == "ACTIVE"
        assert not repository.outbox
    else:
        assert repository.user_status["user-1"] == "DELETING"
        assert len(repository.outbox) == 1
        assert revocations.users == ["user-1"]
    assert sum(not isinstance(item, Exception) for item in results) >= 1


@pytest.mark.asyncio
async def test_failed_cross_domain_cleanup_stays_deleting_and_can_retry() -> None:
    # 功能:验证跨域清理失败时维持注销中状态并允许重试
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    repository = InMemoryAccountRepository()
    repository.seed_user("user-1")
    service = AccountLifecycleService(repository, RevocationRecorder())
    request = await service.request_deletion("user-1", NOW - timedelta(days=7))
    await service.execute_due_deletions(NOW)

    failed = await service.record_cross_domain_cleanup(
        "user-1", request.id, succeeded=False, now=NOW
    )

    assert failed.status == "DELETING"
    assert repository.user_status["user-1"] == "DELETING"
    assert repository.outbox[0].status == "PENDING"

    completed = await service.record_cross_domain_cleanup(
        "user-1", request.id, succeeded=True, now=NOW + timedelta(minutes=1)
    )
    assert completed.status == "DELETED"
    assert repository.user_status["user-1"] == "DELETED"
