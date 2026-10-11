import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from juya_miniapp_api.modules.favorites.snapshots import (
    ConfirmedSnapshot,
    InMemorySnapshotRepository,
    SnapshotCreateReference,
    SnapshotReference,
    SnapshotService,
)
from juya_miniapp_api.shared.errors import AppError

NOW = datetime(2026, 10, 11, tzinfo=UTC)


def reference(sense: str = "cup", version: int = 1) -> SnapshotReference:
    return SnapshotReference(
        content_type="dialogue",
        content_id="coffee",
        content_version=version,
        entry_type="word",
        entry_id="medium-" + sense,
        sense_id=sense,
        source_id="source-" + sense,
    )


class Authority:
    def __init__(self) -> None:
        self.denied = False

    async def confirm_snapshot(self, user_id: str, ref: SnapshotReference) -> ConfirmedSnapshot:
        if self.denied:
            raise AppError("CONTENT_ACCESS_DENIED", "无权", 403)
        return ConfirmedSnapshot(
            reference=ref.model_copy(update={"entry_type": ref.entry_type or "word"}),
            revision_id="revision-" + str(ref.content_version),
            english="medium",
            meaning_zh="中杯" if ref.sense_id == "cup" else "中等的",
            explanation="说明" * 1000,
            source_sentence="A medium coffee, please.",
            source_title="咖啡店",
            source_locator={"sentence_id": "sentence-1"},
            pronunciation={"target_id": "target-" + ref.sense_id, "resource_version": "v1"},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [None, "word"])
async def test_public_request_subtype_matches_exact_authoritative_reference_values(kind):
    payload = SnapshotCreateReference(
        **reference().model_dump(exclude={"entry_type"}), entry_type=kind
    )

    class PublicAuthority(Authority):
        async def confirm_snapshot(self, user_id, ref):
            fixed = await super().confirm_snapshot(user_id, ref)
            return fixed.model_copy(
                update={"reference": SnapshotReference.model_validate(fixed.reference.model_dump())}
            )

    service = SnapshotService(InMemorySnapshotRepository(), PublicAuthority())
    row = await service.create("owner", payload, "public-create", NOW)
    assert row.active and row.data.reference.entry_type == "word"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"content_version": 2},
        {"source_id": "another-source"},
        {"entry_type": "chunk"},
    ],
)
async def test_public_subtype_still_rejects_changed_authoritative_reference_fields(change):
    payload = SnapshotCreateReference.model_validate(reference().model_dump() | change)

    class FixedAuthority(Authority):
        async def confirm_snapshot(self, user_id, ref):
            return await super().confirm_snapshot(user_id, reference())

    with pytest.raises(AppError) as mismatch:
        await SnapshotService(InMemorySnapshotRepository(), FixedAuthority()).create(
            "owner", payload, "mismatched-reference", NOW
        )
    assert mismatch.value.code == "CONTENT_VERSION_CONFLICT"


@pytest.mark.asyncio
async def test_different_medium_senses_and_concurrent_retries_are_independent() -> None:
    repo = InMemorySnapshotRepository()
    service = SnapshotService(repo, Authority())
    rows = await asyncio.gather(
        *[service.create("user", reference(), "key-" + str(i), NOW) for i in range(20)]
    )
    other = await service.create("user", reference("size"), "other", NOW)
    assert len({row.favorite_id for row in rows}) == 1
    assert rows[0].favorite_id != other.favorite_id
    assert rows[0].data.meaning_zh == "中杯"
    assert other.data.meaning_zh == "中等的"


@pytest.mark.asyncio
async def test_repeat_active_preserves_snapshot_cancel_and_recollect_uses_new_revision() -> None:
    repo = InMemorySnapshotRepository()
    service = SnapshotService(repo, Authority())
    first = await service.create("user", reference(), "first", NOW)
    repeated = await service.create("user", reference(version=2), "second", NOW)
    assert repeated.saved_content_version == 1
    assert repeated.snapshot_version == 1
    canceled = await service.cancel("user", first.favorite_id, "cancel", NOW)
    assert not canceled.active
    assert not (await service.cancel("user", first.favorite_id, "cancel-again", NOW)).active
    restored = await service.create("user", reference(version=2), "third", NOW)
    assert restored.favorite_id == first.favorite_id
    assert restored.snapshot_version == 2
    assert restored.saved_content_version == 2
    assert len(repo.events) == 3


@pytest.mark.asyncio
async def test_success_replays_before_authorization_and_detects_payload_change() -> None:
    authority = Authority()
    service = SnapshotService(InMemorySnapshotRepository(), authority)
    first = await service.create("user", reference(), "key", NOW)
    authority.denied = True
    assert await service.create("user", reference(), "key", NOW) == first
    with pytest.raises(AppError) as error:
        await service.create("user", reference("size"), "key", NOW)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_owner_active_and_exact_target_required_for_review_pronunciation() -> None:
    service = SnapshotService(InMemorySnapshotRepository(), Authority())
    row = await service.create("user", reference(), "key", NOW)
    context = await service.review_context("user", row.favorite_id, "target-cup")
    assert context["purpose"] == "FAVORITE_REVIEW"
    assert context["snapshot_version"] == 1
    for user, target in [("other", "target-cup"), ("user", "whole-audio")]:
        with pytest.raises(AppError):
            await service.review_context(user, row.favorite_id, target)
    await service.cancel("user", row.favorite_id, "cancel", NOW)
    with pytest.raises(AppError):
        await service.review_context("user", row.favorite_id, "target-cup")


@pytest.mark.asyncio
async def test_cursor_binds_user_and_filter_and_all_pages_include_full_snapshot() -> None:
    service = SnapshotService(InMemorySnapshotRepository(), Authority())
    for i in range(105):
        await service.create("user", reference(str(i)), str(i), NOW)
    first = await service.page("user", "word", None, 100)
    assert len(first.items) == 100
    assert first.has_more
    second = await service.page("user", "word", first.next_cursor, 100)
    assert len(second.items) == 5
    assert len(second.items[0].data.explanation) == 2000
    for user, kind in [("other", "word"), ("user", "chunk")]:
        with pytest.raises(AppError):
            await service.page(user, kind, first.next_cursor, 100)


def test_reference_rejects_client_supplied_definition_and_chunk_image() -> None:
    with pytest.raises(ValidationError):
        SnapshotReference.model_validate(reference().model_dump() | {"meaning_zh": "伪造"})
    with pytest.raises(ValidationError):
        ConfirmedSnapshot(
            reference=reference().model_copy(update={"entry_type": "chunk"}),
            revision_id="r",
            english="phrase",
            meaning_zh="义",
            explanation="",
            source_sentence="sentence",
            source_title="title",
            source_locator={},
            image_asset_id="image",
        )


@pytest.mark.asyncio
async def test_public_reference_infers_type_from_authoritative_entry() -> None:
    ref = SnapshotReference.model_validate(reference().model_dump(exclude={"entry_type"}))
    repo = InMemorySnapshotRepository()
    authority = Authority()
    service = SnapshotService(repo, authority)
    result = await service.create("user", ref, "infer", NOW)
    assert result.data.reference.entry_type == "word"
    authority.denied = True
    assert await service.create("user", ref, "infer", NOW) == result
