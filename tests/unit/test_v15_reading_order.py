from unittest.mock import AsyncMock

import pytest

from juya_miniapp_api.modules.short_reading.authority import V15ReadingAuthority
from juya_miniapp_api.modules.short_reading.domain import ReadingPreview
from juya_miniapp_api.modules.short_reading.order import ReadingListCursor
from juya_miniapp_api.shared.errors import AppError


def test_reading_cursor_binds_user_and_authoritative_order_snapshot():
    value = ReadingListCursor(-2, "reading-b").encode("owner", "order-v1")
    assert ReadingListCursor.decode("owner", "order-v1", value) == ReadingListCursor(
        -2, "reading-b"
    )
    for user, order in [("stranger", "order-v1"), ("owner", "order-v2")]:
        with pytest.raises(AppError) as expired:
            ReadingListCursor.decode(user, order, value)
        assert expired.value.code == "CURSOR_INVALID"


@pytest.mark.parametrize("value", ["bad", "W10=", "e30=", "x" * 3000])
def test_reading_cursor_rejects_malformed_or_old_id_only_cursor(value):
    with pytest.raises(AppError):
        ReadingListCursor.decode("owner", "order-v1", value)


@pytest.mark.asyncio
async def test_reorder_during_preview_does_not_return_an_inconsistent_page():
    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def scalar(self, *args):
            return 1

    authority = V15ReadingAuthority(lambda: Session(), object())
    authority._order_snapshot = AsyncMock(
        side_effect=[[(1, "reading", "revision")], [(2, "reading", "revision")]]
    )
    authority.preview = AsyncMock(
        return_value=ReadingPreview(
            content_id="reading",
            title_en="Safe",
            title_zh="安全标题",
            category="daily",
            topic="daily",
            level="A1",
            introduction="Safe only",
        )
    )
    with pytest.raises(AppError) as changed:
        await authority.listing("owner", None, 20)
    assert changed.value.code == "CURSOR_INVALID"
