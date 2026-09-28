from datetime import UTC, datetime, timedelta

import pytest

from juya_miniapp_api.infrastructure.security.field_cipher import FieldCipher
from juya_miniapp_api.modules.contacts.repository import InMemoryContactRepository
from juya_miniapp_api.modules.contacts.service import ContactService, normalize_wechat_id
from juya_miniapp_api.shared.errors import AppError

NOW = datetime(2026, 9, 28, 18, 0, tzinfo=UTC)
USER_ID = "01K00000000000000000000001"


def make_service() -> tuple[ContactService, InMemoryContactRepository]:
    repository = InMemoryContactRepository()
    service = ContactService(repository, FieldCipher(b"k" * 32, b"h" * 32))
    return service, repository


def test_normalization_handles_format_only_differences() -> None:
    assert normalize_wechat_id("  \uff34\uff45\uff53\uff54\uff3fID  ") == "test_id"


@pytest.mark.asyncio
async def test_initial_and_same_value_do_not_consume_edit_opportunity() -> None:
    service, repository = make_service()

    initial = await service.save(USER_ID, " Test_ID ", "privacy-v1", "PROFILE", NOW)
    repeated = await service.save(
        USER_ID,
        "\uff54\uff45\uff53\uff54\uff3f\uff49\uff44",
        "privacy-v1",
        "PROFILE",
        NOW + timedelta(minutes=1),
    )

    assert initial.wechat_id == "test_id"
    assert repeated.self_edit_count == 0
    assert repeated.change_pending is False
    assert repeated.contact_status == "PENDING"
    assert repository.records[USER_ID].wechat_id_ciphertext is not None
    assert "test_id" not in repr(repository.records[USER_ID])


@pytest.mark.asyncio
async def test_real_change_is_allowed_once_and_returns_to_pending() -> None:
    service, repository = make_service()
    await service.save(USER_ID, "first-id", "privacy-v1", "PROFILE", NOW)
    await service.update_status(USER_ID, "INVALID", "admin-1", NOW)

    changed = await service.save(
        USER_ID,
        "second-id",
        "privacy-v1",
        "PROFILE",
        NOW + timedelta(minutes=1),
    )

    assert changed.self_edit_count == 1
    assert changed.change_pending is True
    assert changed.contact_status == "PENDING"
    assert repository.records[USER_ID].verified_at is None
    with pytest.raises(AppError) as error:
        await service.save(
            USER_ID,
            "third-id",
            "privacy-v1",
            "PROFILE",
            NOW + timedelta(minutes=2),
        )
    assert error.value.code == "CONTACT_SELF_EDIT_LIMIT"


@pytest.mark.asyncio
async def test_verified_contact_requires_one_active_correction_request() -> None:
    service, _repository = make_service()
    await service.save(USER_ID, "first-id", "privacy-v1", "PROFILE", NOW)
    await service.update_status(USER_ID, "VERIFIED", "admin-1", NOW)

    with pytest.raises(AppError) as direct_edit:
        await service.save(USER_ID, "second-id", "privacy-v1", "PROFILE", NOW)
    assert direct_edit.value.code == "CONTACT_CORRECTION_REQUIRED"

    correction = await service.request_correction(USER_ID, "微信号需要更正", NOW)
    with pytest.raises(AppError) as duplicate:
        await service.request_correction(USER_ID, "再次申请", NOW)
    assert duplicate.value.code == "CONTACT_CORRECTION_ACTIVE"

    await service.decide_correction(correction.public_id, "APPROVED", "admin-1", NOW)
    changed = await service.save(USER_ID, "second-id", "privacy-v1", "PROFILE", NOW)
    assert changed.self_edit_count == 1


@pytest.mark.asyncio
async def test_withdraw_removes_secret_material_and_audit_has_no_plaintext() -> None:
    service, repository = make_service()
    await service.save(USER_ID, "secret-wechat", "privacy-v1", "PROFILE", NOW)

    withdrawn = await service.withdraw(USER_ID, NOW + timedelta(minutes=1))

    record = repository.records[USER_ID]
    assert withdrawn.wechat_id is None
    assert record.wechat_id_ciphertext is None
    assert record.wechat_id_hmac is None
    assert record.change_pending is False
    assert record.contact_status == "NOT_PROVIDED"
    assert all("secret-wechat" not in repr(event) for event in repository.audit_events)
