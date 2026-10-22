import json

import numpy as np
import pandas as pd
import pytest

from tqqq_blend import runner
from tqqq_blend.config import Config, StrategyConfig
from tqqq_blend.explain import explain
from tqqq_blend.strategy import compute_signals


def bars(closes, vols=None, seed=0):
    closes = np.asarray(closes, float)
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2015-01-02", periods=len(closes))
    vols = rng.uniform(5e5, 2e6, len(closes)) if vols is None else np.asarray(vols, float)
    spread = rng.uniform(0.002, 0.015, len(closes))
    return pd.DataFrame(
        {
            "open": closes * (1 - spread / 3),
            "high": closes * (1 + spread),
            "low": closes * (1 - spread),
            "close": closes,
            "volume": vols,
        },
        index=idx,
    )


def random_walk(n, seed, drift=0.0004, vol=0.014):
    rng = np.random.default_rng(seed)
    return bars(100 * np.exp(np.cumsum(rng.normal(drift, vol, n))), seed=seed)


@pytest.fixture(scope="module")
def signals():
    c = StrategyConfig()
    # bull, bear and choppy paths, so every sleeve goes through its states
    frames = [random_walk(1100, 1), random_walk(1100, 2, drift=-0.0006, vol=0.02), random_walk(1100, 3, vol=0.025)]
    return c, [compute_signals(f, c) for f in frames]


def test_blend_reproduces_the_recorded_net_on_every_day(signals):
    c, sigs = signals
    for sig in sigs:
        for i in range(len(sig)):
            why = explain(sig, c, i)
            assert why["net"] == pytest.approx(sig["net"].iloc[i], abs=1e-9), (i, why["blend"])


def test_every_day_explains_without_error_and_is_strict_json(signals):
    c, sigs = signals
    for sig in sigs:
        for i in range(len(sig)):
            why = explain(sig, c, i)
            json.dumps(why, allow_nan=False)
            assert [s["key"] for s in why["sleeves"]] == ["trend", "mr", "vibha"]
            for s in why["sleeves"]:
                assert s["rule"] and isinstance(s["detail"], str)


def test_rules_cover_the_strategy_states(signals):
    c, sigs = signals
    rules = {s["rule"].split(":")[0] for sig in sigs for i in range(len(sig)) for s in explain(sig, c, i)["sleeves"]}
    for expected in ("Uptrend", "Buy the dip", "Fade the rally", "Closed the trade", "No setup", "Out of the market"):
        assert expected in rules, sorted(rules)
    assert any(r.startswith("Holding") for r in rules)
    assert any("Exit below the 21-day EMA" in r or "Follow-through day" in r or "Higher highs" in r for r in rules)


def test_listed_sell_flags_match_the_strategys_count(signals):
    c, sigs = signals
    warm = max(c.exit_ma, c.reclaim_ma, c.vol_avg_len, c.hhhl_days + 1)
    for sig in sigs:
        for i in range(warm, len(sig)):
            if np.isnan(sig["ema_exit"].iloc[i]):
                continue
            flags = explain(sig, c, i)["sleeves"][2].get("flags", [])
            assert len(flags) == sig["sell_flags"].iloc[i], (i, flags)


def test_steady_uptrend_reads_as_a_full_uptrend():
    c = StrategyConfig()
    sig = compute_signals(bars(100 * 1.0006 ** np.arange(600)), c)
    trend = explain(sig, c)["sleeves"][0]
    assert trend["rule"].startswith("Uptrend")
    assert "250-day" in trend["detail"]


# ---- the runner records the reasons, and a failure to explain never changes a run ----


class FakeBroker:
    name = "ibkr"

    def __init__(self):
        self.placed = []

    def connect(self):
        pass

    def disconnect(self):
        pass

    def positions(self):
        return {}

    def net_liquidation(self):
        return 100_000.0

    def place(self, order):
        self.placed.append(order)
        return "Filled"


@pytest.fixture
def fake_run(monkeypatch, tmp_path):
    hist = bars(100 * 1.0008 ** np.arange(600))  # a clean uptrend: the run wants to buy TQQQ
    broker = FakeBroker()
    monkeypatch.setattr(runner, "make_broker", lambda cfg: broker)
    monkeypatch.setattr(runner, "live_signal_bars", lambda *a, **k: hist)
    monkeypatch.setattr(
        runner, "get_prices", lambda cfg, b, syms: ({"QQQ": hist["close"].iloc[-1], "TQQQ": 50.0, "SQQQ": 20.0}, "test")
    )
    cfg = Config()
    cfg.state_dir = str(tmp_path / "state")
    cfg.broker.name = "ibkr"  # dry run: not --live, so nothing is placed
    return cfg, broker


def test_run_records_why_consistent_with_its_decision(fake_run):
    cfg, broker = fake_run
    rec = runner.run_once(cfg, live=False, force=True)
    assert rec["status"] == "dry-run"
    assert rec["why"]["net"] == pytest.approx(rec["net"])
    assert rec["why"]["sleeves"][0]["rule"].startswith("Uptrend")
    assert broker.placed == []


def test_a_failing_explanation_cannot_change_the_run(fake_run, monkeypatch):
    cfg, _ = fake_run
    baseline = runner.run_once(cfg, live=False, force=True)

    def boom(*a, **k):
        raise ValueError("explain broke")

    monkeypatch.setattr(runner, "explain", boom)
    cfg.state_dir = cfg.state_dir + "-2"  # fresh ledger, same inputs
    rec = runner.run_once(cfg, live=False, force=True)
    assert "why" not in rec and rec["why_error"] == "ValueError: explain broke"
    assert rec["status"] == baseline["status"]
    assert rec["orders"] == baseline["orders"]
    assert rec["net"] == baseline["net"]
