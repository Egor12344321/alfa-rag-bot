from datetime import UTC, datetime

import pytest

from alfa_bot.reminders.schedule import (
    ScheduleError,
    calculate_first_run,
    calculate_next_run,
)


def test_monthly_keeps_original_day_after_short_month():
    anchor = datetime(2027, 1, 31, 7, tzinfo=UTC)
    february = calculate_next_run(
        starts_at=anchor,
        frequency="monthly",
        timezone_name="Europe/Moscow",
        after=anchor,
    )
    assert february == datetime(2027, 2, 28, 7, tzinfo=UTC)
    march = calculate_next_run(
        starts_at=anchor,
        frequency="monthly",
        timezone_name="Europe/Moscow",
        after=february,
    )
    assert march == datetime(2027, 3, 31, 7, tzinfo=UTC)


def test_nonexistent_daily_local_time_is_skipped():
    anchor = datetime(2027, 3, 27, 1, 30, tzinfo=UTC)
    next_run = calculate_next_run(
        starts_at=anchor,
        frequency="daily",
        timezone_name="Europe/Berlin",
        after=anchor,
    )
    assert next_run == datetime(2027, 3, 29, 0, 30, tzinfo=UTC)


def test_first_run_rejects_nonexistent_or_past_time():
    with pytest.raises(ScheduleError):
        calculate_first_run(
            local_datetime=datetime(2027, 3, 28, 2, 30),
            timezone_name="Europe/Berlin",
            now=datetime(2027, 3, 1, tzinfo=UTC),
        )
    with pytest.raises(ScheduleError):
        calculate_first_run(
            local_datetime=datetime(2026, 1, 1, 10),
            timezone_name="Europe/Moscow",
            now=datetime(2027, 3, 1, tzinfo=UTC),
        )
