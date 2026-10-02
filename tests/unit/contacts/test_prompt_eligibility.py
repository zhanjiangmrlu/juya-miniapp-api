from datetime import UTC, datetime

import pytest

from juya_miniapp_api.infrastructure.security.field_cipher import FieldCipher
from juya_miniapp_api.modules.contacts.repository import InMemoryContactRepository
from juya_miniapp_api.modules.contacts.service import ContactService
from juya_miniapp_api.modules.users.repository import InMemoryUserRepository
from juya_miniapp_api.modules.users.service import UserService


@pytest.mark.asyncio
async def test_prompt_is_once_per_user_even_across_devices() -> None:
    repository = InMemoryContactRepository()
    now = datetime.now(UTC)
    assert await repository.record_prompt_exposure("user", "device-a", now)
    assert not await repository.record_prompt_exposure("user", "device-b", now)
    assert await repository.has_prompt_exposure("user")
    assert not await repository.has_prompt_exposure("other")


@pytest.mark.asyncio
async def test_me_prompt_requires_three_open_completions_and_no_contact_or_exposure() -> None:
    users = InMemoryUserRepository()
    users.add("user", "100001")
    contacts = InMemoryContactRepository()
    service = ContactService(contacts, FieldCipher(b"k" * 32, b"h" * 32))
    completed = 2

    async def count(_: str) -> int:
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
