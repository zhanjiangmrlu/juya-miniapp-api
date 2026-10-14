import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

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
async def test_revoke_just_before_cutoff_restores_account_without_clearing_data() -> None:
    # 功能:验证截止前一微秒允许撤回且保留学习数据和权益
    # 参数:无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    repository = InMemoryAccountRepository()
    repository.seed_user("user-1")
    repository.learning_rows.add("user-1")
    repository.entitlements.add("user-1")
    service = AccountLifecycleService(repository, RevocationRecorder())
    request = await service.request_deletion("user-1", NOW)
    revoked_at = NOW + timedelta(days=7, microseconds=-1)

    revoked = await service.revoke_deletion("user-1", revoked_at)

    assert revoked.status == "REVOKED"
    assert revoked.revoked_at == revoked_at
    assert revoked.requested_at == NOW
    assert revoked.effective_at == NOW + timedelta(days=7)
    assert repository.user_status["user-1"] == "ACTIVE"
    assert repository.learning_rows == {"user-1"}
    assert repository.entitlements == {"user-1"}
    assert not repository.outbox
    assert request.id == revoked.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "now",
    [
        NOW + timedelta(days=7),
        NOW + timedelta(days=7, microseconds=1),
        NOW + timedelta(days=8),
        (NOW + timedelta(days=7)).astimezone(timezone(timedelta(hours=8))),
        (NOW + timedelta(days=7)).replace(tzinfo=None),
    ],
    ids=["exact-cutoff", "after-cutoff", "worker-delayed-day-eight", "beijing", "naive-utc"],
)
async def test_revoke_at_or_after_cutoff_leaves_pending_request_unchanged(now: datetime) -> None:
    # 功能:验证到期即拒绝撤回,不依赖worker且不改申请、账号或数据
    # 参数:now: 截止或截止后的操作时间,包含不同时间表示。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    repository = InMemoryAccountRepository()
    repository.seed_user("user-1")
    repository.learning_rows.add("user-1")
    repository.entitlements.add("user-1")
    service = AccountLifecycleService(repository, RevocationRecorder())
    request = await service.request_deletion("user-1", NOW)
    before = replace(request)

    with pytest.raises(AppError) as expired:
        await service.revoke_deletion("user-1", now)

    assert expired.value.code == "DELETION_REVOCATION_EXPIRED"
    assert expired.value.status_code == 409
    assert repository.deletions[request.id] == before
    assert repository.user_status["user-1"] == "DELETION_PENDING"
    assert repository.learning_rows == {"user-1"}
    assert repository.entitlements == {"user-1"}
    assert not repository.outbox


@pytest.mark.asyncio
async def test_revoke_uses_stored_effective_at_instead_of_recomputing_seven_days() -> None:
    # 功能:验证截止守卫比较申请实际保存的生效时间
    # 参数:无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    repository = InMemoryAccountRepository()
    repository.seed_user("user-1")
    request = await repository.request_deletion("user-1", NOW, NOW + timedelta(days=1))

    with pytest.raises(AppError) as expired:
        await repository.revoke_deletion("user-1", NOW + timedelta(days=1))

    assert expired.value.code == "DELETION_REVOCATION_EXPIRED"
    assert request.status == "PENDING"
    assert request.revoked_at is None


@pytest.mark.asyncio
async def test_processing_deletion_keeps_existing_revoke_error() -> None:
    # 功能:验证worker已启动时仍返回既有处理中错误
    # 参数:无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    repository = InMemoryAccountRepository()
    repository.seed_user("user-1")
    service = AccountLifecycleService(repository, RevocationRecorder())
    request = await service.request_deletion("user-1", NOW - timedelta(days=7))
    await service.execute_due_deletions(NOW)
    before = replace(request)

    with pytest.raises(AppError) as processing:
        await service.revoke_deletion("user-1", NOW)

    assert processing.value.code == "DELETION_ALREADY_PROCESSING"
    assert request == before


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
    assert current.status == "DELETING"
    assert current.revoked_at is None
    assert repository.user_status["user-1"] == "DELETING"
    assert len(repository.outbox) == 1
    assert revocations.users == ["user-1"]
    assert isinstance(results[0], AppError)
    assert results[0].code in {"DELETION_REVOCATION_EXPIRED", "DELETION_ALREADY_PROCESSING"}
    assert results[1] == [current]


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
