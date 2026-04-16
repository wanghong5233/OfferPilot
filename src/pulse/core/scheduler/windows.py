from __future__ import annotations

from datetime import datetime
from typing import Iterable


def is_weekend(now: datetime) -> bool:
    return now.weekday() >= 5


def is_active_hour(
    now: datetime,
    *,
    weekday_start: int,
    weekday_end: int,
    weekend_start: int,
    weekend_end: int,
) -> bool:
    hour = now.hour
    if is_weekend(now):
        return weekend_start <= hour < weekend_end
    return weekday_start <= hour < weekday_end


def is_peak_hour(
    now: datetime,
    *,
    peak_windows: Iterable[tuple[int, int]],
    weekend_peak: bool = False,
) -> bool:
    if is_weekend(now) and not weekend_peak:
        return False
    hour = now.hour
    return any(start <= hour < end for start, end in peak_windows)
