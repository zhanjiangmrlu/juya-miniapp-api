from datetime import UTC, date, datetime

from juya_miniapp_api.modules.checkins.service import (
    beijing_learning_date,
    summarize_checkins,
)


def test_beijing_learning_date_crosses_at_local_midnight() -> None:
    # 功能:验证学习日期在北京时间午夜切换
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
    assert beijing_learning_date(datetime(2026, 9, 28, 15, 59, tzinfo=UTC)) == date(2026, 9, 28)
    assert beijing_learning_date(datetime(2026, 9, 28, 16, 0, tzinfo=UTC)) == date(2026, 9, 29)


def test_checkin_summary_reports_current_total_and_longest_streak() -> None:
    # 功能:验证打卡汇总正确计算当前、累计和最长连续天数
    # 参数:
    #     无形参。
    # 返回:无返回值;断言失败时由pytest报告测试失败
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
