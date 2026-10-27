"""When a bucket acts. Every sleeve is checked at a month-end close: the last NYSE trading day of the month.

A month-end that passed without a completed check (the server was off, a run failed) is caught up on the
next run. A new bucket waits for its first month-end rather than buying mid-month, because the rules were
tested at month-ends. Quality checks the market filter every month-end but rebalances its stocks only once a
year, when the filter turns, or when it first starts.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pandas_market_calendars as mcal


def trading_days(start: date, end: date) -> list[date]:
    return [d.date() for d in mcal.get_calendar("NYSE").valid_days(start_date=start, end_date=end)]


def is_month_end(day: date) -> bool:
    """Whether `day` is the last NYSE trading day of its month."""
    days = trading_days(day, (pd.Timestamp(day) + pd.offsets.MonthEnd(0)).date())
    return bool(days) and days[-1] == day


def last_month_end_before(day: date) -> date:
    """The last trading day of the most recent month that ended before `day`'s month."""
    first = pd.Timestamp(day).replace(day=1)
    days = trading_days((first - pd.Timedelta(days=10)).date(), (first - pd.Timedelta(days=1)).date())
    return days[-1]


def next_month_end(day: date) -> date:
    """The month-end check on or after `day`."""
    for months in range(0, 3):
        end = (pd.Timestamp(day) + pd.offsets.MonthEnd(months)).date()
        days = trading_days(day, end)
        if days and days[-1] >= day:
            return days[-1]
    raise RuntimeError(f"no NYSE trading day found in the 3 months after {day}")


def check_due(state: dict, today: date) -> tuple[bool, str]:
    """Whether today's run makes the month-end decision, and why (or when the next one is)."""
    if is_month_end(today):
        return True, f"month-end check ({today})"
    last = state.get("last_check")
    if last is None:
        return False, f"waiting for the first month-end check on {next_month_end(today)}"
    missed = last_month_end_before(today)
    if str(missed) > last:
        return True, f"catching up the {missed} month-end check that didn't complete"
    return False, f"next month-end check on {next_month_end(today)}"


def months_between(a: str, b: date) -> int:
    """Whole calendar months from date string `a` to `b` (2026-10-30 -> 2027-10-29 is 12)."""
    x = pd.Timestamp(a)
    return (b.year - x.year) * 12 + (b.month - x.month)


def quality_trade_due(state: dict, today: date, filter_on: bool, every: int) -> tuple[bool, str]:
    """At a quality check: rebalance the stocks if it never has, if `every` months have passed since the
    last one, or if the market filter turned (on: buy stocks; off: move to T-bills)."""
    last = state.get("last_rebalance")
    if last is None:
        return True, "first rebalance"
    if state.get("filter_on") is not None and bool(state["filter_on"]) != filter_on:
        return True, "market filter turned " + ("on" if filter_on else "off")
    if months_between(last, today) >= every:
        return True, f"annual rebalance ({months_between(last, today)} months since {last})"
    return False, f"holding: next annual rebalance due {every - months_between(last, today)} month(s) from now"
