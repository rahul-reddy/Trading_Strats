from datetime import date

import pytest

from sleeves import schedule


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 10, 30), True),  # Friday, last trading day of October
        (date(2026, 10, 29), False),
        (date(2026, 11, 27), False),  # day after Thanksgiving (half day), but not the last trading day
        (date(2026, 11, 30), True),
        (date(2026, 12, 31), True),
        (date(2026, 10, 31), False),  # Saturday
    ],
)
def test_is_month_end(day, expected):
    assert schedule.is_month_end(day) is expected


def test_a_new_bucket_waits_for_its_first_month_end():
    due, why = schedule.check_due({}, date(2026, 10, 27))
    assert not due and "2026-10-30" in why
    assert schedule.check_due({}, date(2026, 10, 30))[0]


def test_a_missed_month_end_is_caught_up():
    due, why = schedule.check_due({"last_check": "2026-09-30"}, date(2026, 11, 2))
    assert due and "2026-10-30" in why
    assert not schedule.check_due({"last_check": "2026-10-30"}, date(2026, 11, 2))[0]


def test_quality_rebalances_yearly_or_when_the_filter_turns():
    today = date(2026, 11, 30)
    assert schedule.quality_trade_due({}, today, True, 12) == (True, "first rebalance")
    held = {"last_rebalance": "2026-10-30", "filter_on": True}
    assert not schedule.quality_trade_due(held, today, True, 12)[0]
    assert schedule.quality_trade_due(held, today, False, 12) == (True, "market filter turned off")
    assert schedule.quality_trade_due(held, date(2027, 10, 29), True, 12)[0]


def test_next_month_end_skips_into_the_next_month():
    assert schedule.next_month_end(date(2026, 10, 31)) == date(2026, 11, 30)
