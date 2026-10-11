from datetime import UTC, datetime

import pytest

from juya_miniapp_api.modules.short_reading.history import HistoryCursor, ReadingHistoryItem
from juya_miniapp_api.shared.errors import AppError


def test_history_summary_never_exposes_body_audio_or_precise_reading_anchor():
    item = ReadingHistoryItem(
        content_id="reading",
        title_en="Title",
        title_zh="标题",
        saved_content_version=1,
        started_at=datetime(2026, 10, 11, tzinfo=UTC),
        last_read_at=datetime(2026, 10, 11, tzinfo=UTC),
        completed_at=None,
        can_open=False,
    )
    assert set(item.model_dump()) == {
        "content_id",
        "title_en",
        "title_zh",
        "saved_content_version",
        "started_at",
        "last_read_at",
        "completed_at",
        "can_open",
    }


def test_history_cursor_is_account_scoped_and_round_trips_stable_order():
    cursor = HistoryCursor(datetime(2026, 10, 11, tzinfo=UTC), "reading")
    encoded = cursor.encode("owner")
    assert HistoryCursor.decode("owner", encoded) == cursor
    with pytest.raises(AppError) as denied:
        HistoryCursor.decode("stranger", encoded)
    assert denied.value.code == "CURSOR_INVALID"


@pytest.mark.parametrize("value", ["bad", "W10=", "e30=", "x" * 3000])
def test_history_rejects_malformed_cursors(value):
    with pytest.raises(AppError) as invalid:
        HistoryCursor.decode("owner", value)
    assert invalid.value.status_code == 422
