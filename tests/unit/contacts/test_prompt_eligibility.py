from datetime import UTC, datetime

import pytest

from juya_miniapp_api.infrastructure.security.field_cipher import FieldCipher
from juya_miniapp_api.modules.contacts.repository import InMemoryContactRepository
from juya_miniapp_api.modules.contacts.service import ContactService
from juya_miniapp_api.modules.users.repository import InMemoryUserRepository
from juya_miniapp_api.modules.users.service import UserService


@pytest.mark.asyncio
async def test_prompt_is_once_per_user_even_across_devices() -> None:
    # 功能:验证联系方式引导跨设备仍对每用户只展示一次
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    repository = InMemoryContactRepository()
    now = datetime.now(UTC)
    assert await repository.record_prompt_exposure("user", "device-a", now)
    assert not await repository.record_prompt_exposure("user", "device-b", now)
    assert await repository.has_prompt_exposure("user")
    assert not await repository.has_prompt_exposure("other")


@pytest.mark.asyncio
async def test_me_prompt_requires_three_open_completions_and_no_contact_or_exposure() -> None:
    # 功能:验证我的页面引导要求完成三个开放场景且无联系方式或曝光
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    users = InMemoryUserRepository()
    users.add("user", "100001")
    contacts = InMemoryContactRepository()
    service = ContactService(contacts, FieldCipher(b"k" * 32, b"h" * 32))
    completed = 2

    async def count(_: str) -> int:
        # 功能:在测试中提供开放场景完成次数以验证联系方式引导资格
        # 参数:
        #     _: 接口约定传入的用户或反馈标识,当前测试桩不依赖具体取值
        # 返回:测试预设的开放场景完成数量
        return completed

    user_service = UserService(users, service, open_completion_count=count)
    assert not (await user_service.get_me("user")).contact_prompt_eligible
    completed = 3
    assert (await user_service.get_me("user")).contact_prompt_eligible
    await contacts.record_prompt_exposure("user", "device-a", datetime.now(UTC))
    assert not (await user_service.get_me("user")).contact_prompt_eligible
    users.add("with-contact", "100002")
    await service.withdraw("with-contact", datetime.now(UTC))
    assert not (await user_service.get_me("with-contact")).contact_prompt_eligible
