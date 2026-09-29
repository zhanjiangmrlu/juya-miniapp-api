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
    await service.update_status(USER_ID, "UNREACHABLE", "admin-1", NOW)

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
async def test_contacted_contact_requires_one_active_correction_request() -> None:
    service, _repository = make_service()
    await service.save(USER_ID, "first-id", "privacy-v1", "PROFILE", NOW)
    await service.update_status(USER_ID, "CONTACTED", "admin-1", NOW)

    with pytest.raises(AppError) as direct_edit:
        await service.save(USER_ID, "second-id", "privacy-v1", "PROFILE", NOW)
    assert direct_edit.value.code == "CONTACT_CORRECTION_REQUIRED"

    correction = await service.request_correction(USER_ID, "微信号需要更正", NOW)
    with pytest.raises(AppError) as duplicate:
        await service.request_correction(USER_ID, "再次申请", NOW)
    assert duplicate.value.code == "CONTACT_CORRECTION_ACTIVE"

    await service.decide_correction(
        correction.public_id,
        "APPROVED",
        "admin-1",
        "approve-correction-1",
        NOW,
    )
    changed = await service.save(USER_ID, "second-id", "privacy-v1", "PROFILE", NOW)
    assert changed.self_edit_count == 1


@pytest.mark.asyncio
async def test_admin_lists_and_reads_corrections_without_inventing_new_wechat() -> None:
    service, repository = make_service()
    repository.admin_identities[USER_ID] = ("JY000000000001", "学习者")
    await service.save(USER_ID, "current-wechat", "privacy-v1", "PROFILE", NOW)
    correction = await service.request_correction(USER_ID, "微信号需要更正", NOW)

    page, total = await service.list_corrections("PENDING", 1, 20)
    detail = await service.get_correction(correction.public_id)

    assert total == 1
    assert page == (detail,)
    assert detail.id == correction.public_id
    assert detail.user_id == USER_ID
    assert detail.juya_number == "JY000000000001"
    assert detail.nickname == "学习者"
    assert detail.wechat_id == "current-wechat"
    assert detail.reason == "微信号需要更正"
    assert detail.timeline[-1].event_type == "CONTACT_CORRECTION_CREATED"
    assert not hasattr(detail, "new_wechat_id")


@pytest.mark.asyncio
async def test_correction_decision_replays_same_key_and_rejects_changed_request() -> None:
    service, _repository = make_service()
    await service.save(USER_ID, "current-wechat", "privacy-v1", "PROFILE", NOW)
    correction = await service.request_correction(USER_ID, "微信号需要更正", NOW)

    first = await service.decide_correction(
        correction.public_id,
        "APPROVED",
        "admin-1",
        "decision-key-1",
        NOW,
    )
    replay = await service.decide_correction(
        correction.public_id,
        "APPROVED",
        "admin-1",
        "decision-key-1",
        NOW + timedelta(minutes=1),
    )

    assert replay == first
    with pytest.raises(AppError) as reused:
        await service.decide_correction(
            correction.public_id,
            "REJECTED",
            "admin-1",
            "decision-key-1",
            NOW + timedelta(minutes=2),
        )
    assert reused.value.code == "IDEMPOTENCY_KEY_REUSED"


@pytest.mark.asyncio
async def test_contact_status_uses_five_business_states_without_changing_verification() -> None:
    service, _repository = make_service()
    await service.save(USER_ID, "current-wechat", "privacy-v1", "PROFILE", NOW)

    unreachable = await service.update_status(USER_ID, "UNREACHABLE", "admin-1", NOW)
    do_not_contact = await service.update_status(
        USER_ID, "DO_NOT_CONTACT", "admin-1", NOW + timedelta(minutes=1)
    )

    assert unreachable.verified_at is None
    assert do_not_contact.contact_status == "DO_NOT_CONTACT"
    assert do_not_contact.verified_at is None
    with pytest.raises(AppError) as legacy_status:
        await service.update_status(USER_ID, "VERIFIED", "admin-1", NOW)
    assert legacy_status.value.code == "CONTACT_STATUS_INVALID"

    verified = await service.verify_change(USER_ID, "admin-1", NOW + timedelta(minutes=2))
    assert verified.contact_status == "DO_NOT_CONTACT"
    assert verified.verified_at == NOW + timedelta(minutes=2)


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
