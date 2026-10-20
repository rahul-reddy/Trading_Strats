import numpy as np
import pandas as pd
import pytest

from tqqq_blend.backtest import run, synthetic_leveraged
from tqqq_blend.config import StrategyConfig
from tqqq_blend.runner import plan_orders
from tqqq_blend.strategy import compute_signals


def bars(closes, vols=None):
    closes = np.asarray(closes, float)
    idx = pd.bdate_range("2015-01-02", periods=len(closes))
    vols = np.full(len(closes), 1e6) if vols is None else np.asarray(vols, float)
    return pd.DataFrame({"open": closes * 0.998, "high": closes * 1.005, "low": closes * 0.995,
                         "close": closes, "volume": vols}, index=idx)


def test_steady_uptrend_is_long_never_short():
    s = compute_signals(bars(100 * 1.0008 ** np.arange(400)), StrategyConfig())
    ready = s[s["ready"]]
    assert (ready["w_short"] == 0).all()
    assert (ready["trend"] > 0).all()
    assert ready["w_long"].iloc[-1] > 0


def test_downtrend_trend_sleeve_flat_when_shorts_disabled():
    s = compute_signals(bars(100 * 0.999 ** np.arange(400)), StrategyConfig(short_enabled=False))
    assert (s.loc[s["ready"], "trend"] <= 0).all()
    assert (s.loc[s["ready"], "w_long"] == 0).all()


def test_no_lookahead_signal_unchanged_by_future_bars():
    rng = np.random.default_rng(0)
    full = bars(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, 700))), rng.uniform(5e5, 2e6, 700))
    c = StrategyConfig()
    a = compute_signals(full.iloc[:600], c)["net"]
    b = compute_signals(full, c)["net"].iloc[:600]
    pd.testing.assert_series_equal(a, b)


def test_weights_within_caps():
    rng = np.random.default_rng(1)
    full = bars(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 800))), rng.uniform(5e5, 2e6, 800))
    c = StrategyConfig(short_enabled=True)
    s = compute_signals(full, c)
    assert s["w_long"].between(0, c.max_long_alloc).all()
    assert s["w_short"].between(0, c.max_short_alloc).all()
    assert ((s["w_long"] > 0) & (s["w_short"] > 0)).sum() == 0


def test_backtest_flat_signal_earns_cash_only():
    b = bars(np.linspace(100, 200, 300))
    sig = compute_signals(b, StrategyConfig())
    sig["w_long"] = 0.0
    sig["w_short"] = 0.0
    rets = synthetic_leveraged(b, pd.Series(0.0001, index=b.index))
    eq = run(sig, rets, band=0.1)["equity"]
    assert eq.iloc[-1] == pytest.approx(1.0001 ** 299)


def test_plan_orders_sells_first_and_respects_band():
    prices = {"TQQQ": 50.0, "SQQQ": 10.0}
    orders = plan_orders({"TQQQ": 0.0, "SQQQ": 0.3}, {"TQQQ": 100}, prices, 10_000, 0.1, "MOC")
    assert [(o.symbol, o.side, o.quantity) for o in orders] == [("TQQQ", "SELL", 100), ("SQQQ", "BUY", 300)]
    # 0.50 -> 0.55 is inside a 0.10 band: no trade
    assert plan_orders({"TQQQ": 0.55, "SQQQ": 0.0}, {"TQQQ": 100}, prices, 10_000, 0.1, "MOC") == []


def test_vol_target_scales_long_only_when_volatile():
    rng = np.random.default_rng(2)
    calm = 100 * np.exp(np.cumsum(rng.normal(0.0008, 0.004, 400)))   # ~6% annualized vol
    wild = calm[-1] * np.exp(np.cumsum(rng.normal(0.002, 0.04, 60)))  # ~60% annualized vol
    b = bars(np.concatenate([calm, wild]))
    on, off = compute_signals(b, StrategyConfig()), compute_signals(b, StrategyConfig(vol_target=0))
    assert (on["w_long"] <= off["w_long"] + 1e-12).all()
    assert on["vol_scale"].iloc[300:399].eq(1.0).all()
    hot = on.index[-1]
    if off.loc[hot, "w_long"] > 0:
        assert on.loc[hot, "w_long"] == pytest.approx(off.loc[hot, "w_long"] * on.loc[hot, "vol_scale"])
        assert on.loc[hot, "vol_scale"] < 0.6
