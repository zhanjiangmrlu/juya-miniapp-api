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
    return now.astimezone(BEIJING).date()


def summarize_checkins(days: list[date], *, today: date) -> CheckinSummary:
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
