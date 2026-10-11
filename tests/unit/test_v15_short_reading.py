from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from juya_miniapp_api.modules.short_reading.domain import (
    Paragraph,
    ReadingDetail,
    ReadingPosition,
    ReadingPreview,
)
from juya_miniapp_api.modules.short_reading.repository import InMemoryReadingRepository
from juya_miniapp_api.modules.short_reading.service import ShortReadingService
from juya_miniapp_api.shared.errors import AppError

NOW = datetime(2026, 10, 11, tzinfo=UTC)


def detail(version: int = 1) -> ReadingDetail:
    return ReadingDetail(
        content_id="reading",
        content_version=version,
        revision_id="r" + str(version),
        title_en="A day",
        title_zh="一天",
        category="生活",
        topic="生活",
        level="初级",
        introduction="简介",
        paragraphs_en=[Paragraph(paragraph_id="p1", english="Hello.")],
        translation_zh="你好。",
        whole_audio=None,
    )


class Authority:
    denied = False
    version = 1

    async def open_reading(self, user_id: str, reading_id: str) -> ReadingDetail:
        if self.denied:
            raise AppError("MODULE_DISABLED", "模块关闭", 403)
        return detail(self.version)


@pytest.mark.asyncio
async def test_open_save_complete_are_independent_and_preserve_completion_across_versions() -> None:
    repo = InMemoryReadingRepository()
    authority = Authority()
    service = ShortReadingService(authority, repo)
    opened = await service.open("user", "reading", NOW)
    assert opened.progress is not None
    assert opened.progress.started_at == NOW
    assert opened.whole_audio is None
    await service.save(
        "user",
        "reading",
        ReadingPosition(
            content_version=1,
            paragraph_id="p1",
            offset=3,
            client_sequence=1,
        ),
        NOW,
    )
    first = await service.complete("user", "reading", "done", 1, NOW)
    second = await service.complete("user", "reading", "done-again", 1, NOW)
    assert first.completed_at == second.completed_at == NOW
    assert len(repo.completion_events) == 1
    authority.version = 2
    refreshed = await service.open("user", "reading", NOW)
    assert refreshed.progress.completed_at == NOW
    assert refreshed.progress.paragraph_id is None
    assert refreshed.progress.saved_content_version == 2


@pytest.mark.asyncio
async def test_direct_progress_completion_and_open_recheck_access() -> None:
    authority = Authority()
    repo = InMemoryReadingRepository()
    service = ShortReadingService(authority, repo)
    await service.open("user", "reading", NOW)
    authority.denied = True
    with pytest.raises(AppError):
        await service.open("user", "reading", NOW)
    with pytest.raises(AppError):
        await service.save(
            "user",
            "reading",
            ReadingPosition(
                content_version=1,
                paragraph_id="p1",
                offset=0,
            ),
            NOW,
        )
    with pytest.raises(AppError):
        await service.complete("user", "reading", "done", 1, NOW)
    assert (await repo.get("user", "reading")).completed_at is None


@pytest.mark.asyncio
async def test_stale_versions_and_invalid_positions_do_not_write() -> None:
    repo = InMemoryReadingRepository()
    service = ShortReadingService(Authority(), repo)
    for version, paragraph, offset in [(2, "p1", 0), (1, "missing", 0), (1, "p1", 999)]:
        with pytest.raises(AppError):
            await service.save(
                "user",
                "reading",
                ReadingPosition(
                    content_version=version,
                    paragraph_id=paragraph,
                    offset=offset,
                ),
                NOW,
            )
    assert await repo.get("user", "reading") is None


def test_pure_reading_and_safe_preview_reject_content_leaks() -> None:
    with pytest.raises(ValidationError):
        ReadingDetail.model_validate(detail().model_dump() | {"words": []})
    preview = {
        "content_id": "reading",
        "title_en": "Title",
        "title_zh": "标题",
        "category": "生活",
        "topic": "生活",
        "level": "初级",
        "introduction": "简介",
    }
    for extra in [
        {"paragraphs_en": []},
        {"translation_zh": "泄漏"},
        {"whole_audio": {}},
        {"source_locator": {"paragraph_id": "p1"}},
    ]:
        with pytest.raises(ValidationError):
            ReadingPreview.model_validate(preview | extra)


@pytest.mark.asyncio
async def test_client_sequence_rejects_same_version_out_of_order_and_resets_new_version() -> None:
    repo = InMemoryReadingRepository()
    authority = Authority()
    service = ShortReadingService(authority, repo)
    newest = await service.save(
        "user",
        "reading",
        ReadingPosition(content_version=1, paragraph_id="p1", offset=5, client_sequence=9),
        NOW,
    )
    for sequence in (8, 9):
        stale = await service.save(
            "user",
            "reading",
            ReadingPosition(
                content_version=1, paragraph_id="p1", offset=1, client_sequence=sequence
            ),
            NOW,
        )
        assert stale.offset == newest.offset == 5
        assert stale.client_sequence == 9
    authority.version = 2
    reset = await service.open("user", "reading", NOW)
    assert reset.progress.client_sequence == 0
    saved = await service.save(
        "user",
        "reading",
        ReadingPosition(content_version=2, paragraph_id="p1", offset=2, client_sequence=1),
        NOW,
    )
    assert saved.client_sequence == 1 and saved.offset == 2


@pytest.mark.asyncio
async def test_open_queued_behind_save_keeps_latest_position_in_lock() -> None:
    import asyncio

    repo = InMemoryReadingRepository()
    service = ShortReadingService(Authority(), repo)
    await service.save(
        "user",
        "reading",
        ReadingPosition(content_version=1, paragraph_id="p1", offset=1, client_sequence=1),
        NOW,
    )
    await repo._lock.acquire()
    saved = asyncio.create_task(
        service.save(
            "user",
            "reading",
            ReadingPosition(content_version=1, paragraph_id="p1", offset=5, client_sequence=2),
            NOW,
        )
    )
    await asyncio.sleep(0)
    opened = asyncio.create_task(service.open("user", "reading", NOW))
    await asyncio.sleep(0)
    repo._lock.release()
    await saved
    result = await opened
    assert result.progress.offset == 5
    assert result.progress.client_sequence == 2


@pytest.mark.asyncio
async def test_completion_queued_behind_save_keeps_latest_position_in_lock() -> None:
    import asyncio

    repo = InMemoryReadingRepository()
    service = ShortReadingService(Authority(), repo)
    await service.save(
        "user",
        "reading",
        ReadingPosition(content_version=1, paragraph_id="p1", offset=1, client_sequence=1),
        NOW,
    )
    await repo._lock.acquire()
    saved = asyncio.create_task(
        service.save(
            "user",
            "reading",
            ReadingPosition(content_version=1, paragraph_id="p1", offset=5, client_sequence=2),
            NOW,
        )
    )
    await asyncio.sleep(0)
    completed = asyncio.create_task(service.complete("user", "reading", "complete", 1, NOW))
    await asyncio.sleep(0)
    repo._lock.release()
    await saved
    result = await completed
    assert result.offset == 5
    assert result.client_sequence == 2
    assert result.created is True


@pytest.mark.asyncio
async def test_complete_keys_bind_user_content_and_version_and_recheck_current_access() -> None:
    repo = InMemoryReadingRepository()
    authority = Authority()
    service = ShortReadingService(authority, repo)
    first = await service.complete("user", "reading", "same", 1, NOW)
    assert first.created
    again = await service.complete("user", "reading", "new", 1, NOW)
    assert not again.created
    authority.version = 2
    assert await service.complete("user", "reading", "same", 1, NOW) == first
    with pytest.raises(AppError) as conflict:
        await service.complete("user", "reading", "same", 2, NOW)
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    authority.denied = True
    with pytest.raises(AppError) as denied:
        await service.complete("user", "reading", "same", 1, NOW)
    assert denied.value.status_code == 403
    authority.denied = False
    authority.version = 1
    assert (await service.complete("other-user", "reading", "same", 1, NOW)).created
    assert len(repo.completion_events) == 2


@pytest.mark.asyncio
async def test_replay_rechecks_facts_under_repository_lock_without_requiring_old_version() -> None:
    current = 1
    allowed = True

    async def guard(user_id: str, content_id: str, version: int | None) -> None:
        assert repo._lock.locked()
        if not allowed:
            raise AppError("CONTENT_ACCESS_DENIED", "授权已撤回", 403)
        if version is not None and version != current:
            raise AppError("CONTENT_VERSION_CONFLICT", "短文已更新", 409)

    repo = InMemoryReadingRepository(guard)
    authority = Authority()
    service = ShortReadingService(authority, repo)
    first = await service.complete("user", "reading", "done", 1, NOW)
    current = authority.version = 2
    assert await service.complete("user", "reading", "done", 1, NOW) == first
    # HTTP authority can have succeeded before policy changes at the final lock boundary.
    allowed = False
    for operation in (
        service.complete("user", "reading", "done", 1, NOW),
        repo.replay_completion("user", "reading", "done", 1),
        repo.complete("user", "reading", "done", 1, {"p1": 6}, NOW),
    ):
        with pytest.raises(AppError) as denied:
            await operation
        assert denied.value.status_code == 403
    assert len(repo.completion_events) == 1


@pytest.mark.asyncio
async def test_invalid_saved_anchor_is_reset_in_open_and_complete_without_erasing_completion() -> (
    None
):
    repo = InMemoryReadingRepository()
    service = ShortReadingService(Authority(), repo)
    # Stored progress may reference a removed anchor even with a legacy saved version.
    await repo.save(
        "user",
        "reading",
        ReadingPosition(content_version=1, paragraph_id="gone", offset=4, client_sequence=1),
        NOW,
    )
    completed = await service.complete("user", "reading", "done", 1, NOW)
    assert completed.paragraph_id is None and completed.offset == 0
    opened = await service.open("user", "reading", NOW)
    assert opened.progress.completed_at == NOW
    assert opened.progress.paragraph_id is None
