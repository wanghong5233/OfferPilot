from __future__ import annotations

from datetime import datetime

from pulse.core.scheduler.windows import is_active_hour, is_peak_hour, is_weekend


def test_is_weekend() -> None:
    assert is_weekend(datetime(2026, 3, 28, 10, 0, 0)) is True
    assert is_weekend(datetime(2026, 3, 27, 10, 0, 0)) is False


def test_is_active_hour_weekday_and_weekend() -> None:
    weekday = datetime(2026, 3, 27, 11, 0, 0)
    weekend = datetime(2026, 3, 28, 9, 0, 0)
    assert (
        is_active_hour(
            weekday,
            weekday_start=9,
            weekday_end=22,
            weekend_start=10,
            weekend_end=20,
        )
        is True
    )
    assert (
        is_active_hour(
            weekend,
            weekday_start=9,
            weekday_end=22,
            weekend_start=10,
            weekend_end=20,
        )
        is False
    )


def test_is_peak_hour() -> None:
    weekday_peak = datetime(2026, 3, 27, 15, 0, 0)
    weekday_non_peak = datetime(2026, 3, 27, 21, 0, 0)
    assert is_peak_hour(weekday_peak, peak_windows=[(10, 12), (14, 18)]) is True
    assert is_peak_hour(weekday_non_peak, peak_windows=[(10, 12), (14, 18)]) is False
