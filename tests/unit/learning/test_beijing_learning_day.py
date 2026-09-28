from datetime import UTC, date, datetime

from juya_miniapp_api.modules.checkins.service import (
    beijing_learning_date,
    summarize_checkins,
)


def test_beijing_learning_date_crosses_at_local_midnight() -> None:
    assert beijing_learning_date(datetime(2026, 9, 28, 15, 59, tzinfo=UTC)) == date(2026, 9, 28)
    assert beijing_learning_date(datetime(2026, 9, 28, 16, 0, tzinfo=UTC)) == date(2026, 9, 29)


def test_checkin_summary_reports_current_total_and_longest_streak() -> None:
    summary = summarize_checkins(
        [
            date(2026, 9, 20),
            date(2026, 9, 21),
            date(2026, 9, 25),
            date(2026, 9, 26),
            date(2026, 9, 27),
        ],
        today=date(2026, 9, 28),
    )

    assert summary.total_days == 5
    assert summary.longest_streak == 3
    assert summary.current_streak == 3
