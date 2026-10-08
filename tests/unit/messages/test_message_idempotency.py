from datetime import UTC, datetime

import pytest

from juya_miniapp_api.modules.messages.repository import InMemoryMessageRepository
from juya_miniapp_api.modules.messages.service import MessageService

NOW = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_duplicate_business_event_creates_one_message_and_read_is_idempotent() -> None:
    # 功能:验证重复业务事件只创建一条消息且标记已读幂等
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    repository = InMemoryMessageRepository()
    service = MessageService(repository)

    first = await service.create(
        "user-1",
        "event-1",
        "FEEDBACK_RESOLVED",
        "反馈已处理",
        "问题已有处理结果",
        "FEEDBACK",
        "feedback-1",
        NOW,
    )
    duplicate = await service.create(
        "user-1",
        "event-1",
        "FEEDBACK_RESOLVED",
        "反馈已处理",
        "重复投递不应创建第二条",
        "FEEDBACK",
        "feedback-1",
        NOW,
    )

    assert duplicate.id == first.id
    assert len((await service.list_messages("user-1", limit=20)).items) == 1

    read_once = await service.mark_read("user-1", first.id, NOW)
    read_twice = await service.mark_read("user-1", first.id, NOW)

    assert read_once.read_at == NOW
    assert read_twice.read_at == NOW


@pytest.mark.asyncio
async def test_message_cannot_be_read_by_another_user() -> None:
    # 功能:验证其他用户不能读取不属于自己的消息
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    service = MessageService(InMemoryMessageRepository())
    message = await service.create(
        "user-1",
        "event-2",
        "SYSTEM",
        "系统消息",
        "摘要",
        None,
        None,
        NOW,
    )

    with pytest.raises(Exception) as error:
        await service.mark_read("user-2", message.id, NOW)

    assert getattr(error.value, "code", None) == "MESSAGE_NOT_FOUND"
