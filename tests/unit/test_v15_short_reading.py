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
