from datetime import UTC, datetime, timedelta

import pytest

from juya_miniapp_api.infrastructure.tasks.outbox import (
    InMemoryOutboxStore,
    OutboxDispatcher,
)
from juya_miniapp_api.modules.accounts.domain import OutboxEvent

NOW = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_outbox_retries_with_exponential_backoff_then_dead_letters() -> None:
    # 功能:验证发件箱失败按指数退避重试并最终进入死信
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    event = OutboxEvent(
        "event-1",
        "ACCOUNT_DELETION_CLEANUP",
        "deletion-1",
        {"user_id": "user-1"},
        "PENDING",
        0,
        NOW,
        NOW,
    )
    store = InMemoryOutboxStore([event])

    async def fail(_event: OutboxEvent) -> None:
        # 功能:在测试中模拟发件箱投递失败以验证退避与死信
        # 参数:
        #     _event: 模拟投递失败的发件箱事件,测试桩不读取具体字段
        # 返回:无返回值。
        raise RuntimeError("upstream unavailable")

    dispatcher = OutboxDispatcher(store, fail, max_attempts=3, base_delay=timedelta(seconds=10))

    await dispatcher.dispatch_due(NOW)
    assert event.status == "FAILED"
    assert event.attempt_count == 1
    assert event.next_attempt_at == NOW + timedelta(seconds=10)

    await dispatcher.dispatch_due(NOW + timedelta(seconds=10))
    assert event.status == "FAILED"
    assert event.attempt_count == 2
    assert event.next_attempt_at == NOW + timedelta(seconds=30)

    await dispatcher.dispatch_due(NOW + timedelta(seconds=30))
    assert event.status == "DEAD"
    assert event.attempt_count == 3
    assert event.next_attempt_at is None


@pytest.mark.asyncio
async def test_outbox_delivers_each_event_once() -> None:
    # 功能:验证发件箱每条事件只成功投递一次
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    event = OutboxEvent(
        "event-1",
        "ACCOUNT_DELETION_CLEANUP",
        "deletion-1",
        {"user_id": "user-1"},
        "PENDING",
        0,
        NOW,
        NOW,
    )
    store = InMemoryOutboxStore([event])
    delivered: list[str] = []

    async def deliver(item: OutboxEvent) -> None:
        # 功能:在测试中收集发件箱投递事件以验证恰好一次投递
        # 参数:
        #     item: 跨域清理发件箱事件
        # 返回:无返回值。
        delivered.append(item.id)

    dispatcher = OutboxDispatcher(store, deliver)

    await dispatcher.dispatch_due(NOW)
    await dispatcher.dispatch_due(NOW + timedelta(hours=1))

    assert delivered == ["event-1"]
    assert event.status == "DELIVERED"
    assert event.processed_at == NOW


@pytest.mark.asyncio
async def test_processing_event_is_recovered_after_worker_lease_expires() -> None:
    # 功能:验证worker租约到期后可重新领取处理中的事件
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    event = OutboxEvent(
        "event-1",
        "ACCOUNT_DELETION_CLEANUP",
        "deletion-1",
        {"user_id": "user-1"},
        "PENDING",
        0,
        NOW,
        NOW,
    )
    store = InMemoryOutboxStore([event])

    assert [item.id for item in await store.claim_due(NOW, 10)] == ["event-1"]
    assert await store.claim_due(NOW + timedelta(minutes=4), 10) == []
    assert [item.id for item in await store.claim_due(NOW + timedelta(minutes=5), 10)] == [
        "event-1"
    ]


@pytest.mark.asyncio
async def test_cleanup_callback_wins_race_with_dispatch_failure_writeback() -> None:
    # 功能:验证成功清理回执不被并发的投递失败回写覆盖
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    event = OutboxEvent(
        "event-1",
        "ACCOUNT_DELETION_CLEANUP",
        "deletion-1",
        {"user_id": "user-1"},
        "PENDING",
        0,
        NOW,
        NOW,
    )
    store = InMemoryOutboxStore([event])
    await store.claim_due(NOW, 10)

    await store.mark_delivered(event.id, NOW)
    await store.mark_failed(
        event.id,
        attempt_count=1,
        next_attempt_at=NOW + timedelta(seconds=30),
        dead=False,
    )

    assert event.status == "DELIVERED"
    assert event.processed_at == NOW
