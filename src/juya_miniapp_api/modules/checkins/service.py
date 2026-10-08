from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class CheckinSummary:
    current_streak: int
    total_days: int
    longest_streak: int


def beijing_learning_date(now: datetime) -> date:
    # 功能:将操作时间换算为北京时间学习日期
    # 参数:
    #     now: 本次操作的当前时间,用于有效期、时间戳及业务记录
    # 返回:操作时间对应的北京时间学习日期
    return now.astimezone(BEIJING).date()


def summarize_checkins(days: list[date], *, today: date) -> CheckinSummary:
    # 功能:计算累计、当前连续与最长连续打卡天数
    # 参数:
    #     days: 用户已有的北京时间打卡日期
    #     today: 计算连续打卡时采用的北京时间当天日期
    # 返回:累计、当前连续与最长连续打卡天数
    ordered = sorted(set(days))
    if not ordered:
        return CheckinSummary(0, 0, 0)
    longest = 1
    running = 1
    for previous, current in pairwise(ordered):
        if current == previous + timedelta(days=1):
            running += 1
            longest = max(longest, running)
        else:
            running = 1
    current_streak = 0
    latest = ordered[-1]
    if latest in {today, today - timedelta(days=1)}:
        current_streak = 1
        cursor = latest
        for current in reversed(ordered[:-1]):
            if current == cursor - timedelta(days=1):
                current_streak += 1
                cursor = current
            else:
                break
    return CheckinSummary(current_streak, len(ordered), longest)
