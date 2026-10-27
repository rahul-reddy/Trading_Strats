from dataclasses import replace

import pandas as pd
import pytest
from conftest import closes, screen

from sleeves import rules
from sleeves.config import ROTATION_UNIVERSE, SECTOR_UNIVERSE, SleeveConfig

ROT = replace(SleeveConfig(), kind="rotation")
SEC = replace(SleeveConfig(), kind="sectors")
QUAL = replace(SleeveConfig(), kind="quality")


def test_rotation_holds_the_top_eligible_and_fills_empty_slots_with_tbills():
    up = {"GLD": 0.003, "QQQ": 0.002, "DBC": 0.0025}  # three clear uptrends, everyone else falling
    drift = {s: up.get(s, -0.001) for s in ROTATION_UNIVERSE}
    d = rules.rotation(closes(ROTATION_UNIVERSE, drift=drift), ROT)
    assert d.weights == {"GLD": 0.25, "DBC": 0.25, "QQQ": 0.25, "SGOV": 0.25}
    assert d.reasons[0]["weight"] == 0.25 and "not eligible" in d.reasons[-1]["text"]
    assert sum(d.weights.values()) == pytest.approx(1.0)


def test_rotation_all_in_tbills_when_nothing_trends():
    d = rules.rotation(closes(ROTATION_UNIVERSE, drift={s: -0.002 for s in ROTATION_UNIVERSE}), ROT)
    assert d.weights == {"SGOV": 1.0} and "nothing eligible" in d.summary


def test_rotation_refuses_short_history():
    with pytest.raises(ValueError, match="days of history"):
        rules.rotation(closes(ROTATION_UNIVERSE, days=200), ROT)


def test_sectors_top_three_by_12_minus_1_momentum_when_the_market_is_up():
    uni = SECTOR_UNIVERSE + ["SPY"]
    drift = {"XLE": 0.003, "XLU": 0.0025, "XLK": 0.002, "SPY": 0.001}
    d = rules.sectors(closes(uni, drift=drift), SEC)
    assert set(d.weights) == {"XLE", "XLU", "XLK"} and all(w == pytest.approx(1 / 3) for w in d.weights.values())
    assert "above its 10-month average" in d.summary


def test_sectors_in_tbills_when_the_market_filter_is_off():
    uni = SECTOR_UNIVERSE + ["SPY"]
    d = rules.sectors(closes(uni, drift={"SPY": -0.002, "XLE": 0.003}), SEC)
    assert d.weights == {"SGOV": 1.0} and "below" in d.summary


def test_sectors_leave_out_a_young_etf():
    uni = SECTOR_UNIVERSE + ["SPY"]
    c = closes(uni, drift={"XLRE": 0.01, "SPY": 0.001})
    c.loc[c.index[:200], "XLRE"] = float("nan")  # only ~200 days of history
    assert "XLRE" not in rules.sectors(c, SEC).weights


def test_market_filter_needs_enough_month_ends():
    with pytest.raises(ValueError, match="month-ends"):
        rules.market_filter(closes(["SPY"], days=150)["SPY"], 10)


def test_market_filter_uses_month_end_closes():
    idx = pd.bdate_range("2025-01-01", "2026-10-29")
    s = pd.Series(100.0, index=idx, name="SPY")
    s.iloc[-1] = 101.0  # today above every earlier month-end
    assert rules.market_filter(s, 10)[0] is True
    s.iloc[-1] = 99.0
    assert rules.market_filter(s, 10)[0] is False


def test_quality_picks_cheap_and_profitable_with_a_sector_cap():
    c = closes(["SPY"], drift={"SPY": 0.001})
    d = rules.quality(screen(), c, QUAL)
    picked = list(d.weights)
    assert len(picked) == 25 and all(t < "Q030" for t in picked)
    sectors = screen().set_index("Ticker").loc[picked, "Sector"].value_counts()
    assert sectors.max() <= 5
    assert sum(d.weights.values()) == pytest.approx(1.0) and "P/B" in d.reasons[0]["text"]


def test_quality_skips_negative_book_and_loss_makers():
    s = screen()
    s.loc[0, "P/B"] = -2.0  # negative book value
    s.loc[1, "Operating Margin"] = -0.1
    d = rules.quality(s, closes(["SPY"], drift={"SPY": 0.001}), QUAL)
    assert "Q000" not in d.weights and "Q001" not in d.weights


def test_quality_tbills_when_filter_off():
    d = rules.quality(screen(), closes(["SPY"], drift={"SPY": -0.002}), QUAL)
    assert d.weights == {"SGOV": 1.0}


@pytest.mark.parametrize(("change", "match"), [("short", "only"), ("nocol", "missing columns")])
def test_quality_refuses_a_broken_screen(change, match):
    s = screen().head(100) if change == "short" else screen().drop(columns=["ROE"])
    with pytest.raises(ValueError, match=match):
        rules.quality(s, closes(["SPY"]), QUAL)
