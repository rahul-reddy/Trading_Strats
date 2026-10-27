"""The near-close run and the post-close reconcile against a fake IBKR-like account shared by two buckets.
Market data, signals and the clock are stubbed; everything from the gates to the ledgers is the real code."""

import csv
import json
from itertools import count
from pathlib import Path

import pandas as pd
import pytest

from tqqq_blend import runner
from tqqq_blend.brokers import Fill
from tqqq_blend.config import Config

ET = "America/New_York"
PRICES = {"QQQ": 500.0, "TQQQ": 50.0, "SQQQ": 20.0}


class Account:
    """One broker account: MOC orders wait for the close; market orders fill at once."""

    def __init__(self):
        self.positions: dict[str, int] = {}
        self.fills: list[Fill] = []
        self.queued: list = []
        self.ids = count(1)

    def execute(self, order):
        self.positions[order.symbol] = self.positions.get(order.symbol, 0) + order.signed_qty
        now = pd.Timestamp.now(tz=ET).isoformat()
        px = PRICES[order.symbol]
        self.fills.append(Fill(f"x{next(self.ids)}", order.ref, order.symbol, order.signed_qty, px, 1.0, now))

    def close(self):
        for o in self.queued:
            self.execute(o)
        self.queued = []


class FakeBroker:
    name = "ibkr"

    def __init__(self, account):
        self.account = account
        self.placed = []

    def connect(self):
        pass

    def disconnect(self):
        pass

    def positions(self):
        return {k: v for k, v in self.account.positions.items() if v}

    def net_liquidation(self):
        return 1e9

    def last_price(self, symbol):
        return PRICES[symbol]

    def place(self, order):
        self.placed.append(order)
        if order.order_type == "MOC":
            self.account.queued.append(order)
        else:
            self.account.execute(order)
        return f"fake#{len(self.placed)} Submitted"

    def fills(self, ref):
        return [f for f in self.account.fills if f.ref == ref]


def signals(w_long, w_short=0.0):
    today = pd.Timestamp.now(tz=ET).tz_localize(None).normalize()
    idx = pd.DatetimeIndex([today - pd.Timedelta(days=1), today])
    row = {"trend": 1.0, "mr": 0.0, "vibha": 0.5, "vol_scale": 1.0, "net": w_long - w_short, "dist_days": 0}
    row |= {"sell_flags": 0, "vibha_event": "", "w_long": w_long, "w_short": w_short}
    return pd.DataFrame([row | {"close": 499.0}, row | {"close": 500.0}], index=idx)


@pytest.fixture
def market(monkeypatch):
    """A shared account and controls: the target weight, the minutes to the close."""
    state = {"w_long": 1.0, "mtc": 20.0, "account": Account()}
    monkeypatch.setattr(runner, "get_prices", lambda cfg, broker, syms: ({s: PRICES[s] for s in syms}, "finviz"))
    monkeypatch.setattr(runner, "live_signal_bars", lambda *a: None)
    monkeypatch.setattr(runner, "compute_signals", lambda bars, s: signals(state["w_long"]))
    monkeypatch.setattr(runner, "explain", lambda sig, s: {"sleeves": []})
    monkeypatch.setattr(runner, "minutes_to_close", lambda now=None: state["mtc"])
    monkeypatch.setattr(runner, "make_broker", lambda cfg: FakeBroker(state["account"]))
    return state


def bucket(tmp_path, name, capital):
    cfg = Config()
    cfg.state_dir = str(tmp_path / name)
    cfg.bucket, cfg.account_group = f"tqqq-blend/{name}", "ibkr-paper"
    cfg.risk.capital, cfg.risk.max_order_value = capital, 1e9
    cfg.broker.name, cfg.broker.live_trading_enabled = "ibkr", True
    return cfg


def state(cfg):
    return json.loads((Path(cfg.state_dir) / "state.json").read_text())


def after_close(market, cfg):
    market["account"].close()
    market["mtc"] = -10.0
    rec = runner.reconcile(cfg)
    market["mtc"] = 20.0
    return rec


def test_two_buckets_in_one_account_each_own_only_their_fills(tmp_path, market):
    big, small = bucket(tmp_path, "paper", 100_000), bucket(tmp_path, "mix", 20_000)
    rec = runner.run_once(big, live=True)
    assert rec["status"] == "executed" and rec["orders"] == [
        {"symbol": "TQQQ", "side": "BUY", "quantity": 2000, "order_type": "MOC", "ref": "tqqq-blend/paper"}
    ]
    # before the close the order is pending; the second bucket sees the account unchanged and trades its own
    rec2 = runner.run_once(small, live=True)
    assert rec2["status"] == "executed" and rec2["orders"][0]["quantity"] == 400
    assert after_close(market, big)["status"] == "reconciled"
    assert after_close(market, small)["status"] == "reconciled"
    assert market["account"].positions == {"TQQQ": 2400}
    assert state(big)["positions"] == {"TQQQ": 2000} and state(small)["positions"] == {"TQQQ": 400}
    assert state(big)["strategy_cash"] == pytest.approx(100_000 - 2000 * 50 - 1)
    assert state(big)["pending_orders"] == [] and state(small)["pending_orders"] == []


def test_equity_counts_only_the_buckets_own_shares(tmp_path, market):
    big, small = bucket(tmp_path, "paper", 100_000), bucket(tmp_path, "mix", 20_000)
    runner.run_once(big, live=True)
    after_close(market, big)
    market["w_long"] = 0.0  # the small bucket wants nothing: it must not sell the big bucket's shares
    rec = runner.run_once(small, live=True)
    assert rec["status"] == "no change" and rec["positions"] == {} and rec["equity"] == pytest.approx(20_000)


def test_an_unexplained_position_stops_the_run(tmp_path, market):
    cfg = bucket(tmp_path, "paper", 100_000)
    market["account"].positions["TQQQ"] = 7  # someone traded TQQQ by hand
    rec = runner.run_once(cfg, live=True)
    assert rec["status"].startswith("error: account positions don't match") and not market["account"].queued


def test_orders_never_matched_to_fills_stop_the_next_day(tmp_path, market):
    cfg = bucket(tmp_path, "paper", 100_000)
    runner.run_once(cfg, live=True)  # the MOC order never gets reconciled
    data = state(cfg)
    data["pending_orders"][0]["date"] = "2026-01-02"
    data["last_completed_run"] = "2026-01-02"
    (tmp_path / "paper" / "state.json").write_text(json.dumps(data))
    rec = runner.run_once(cfg, live=True)
    assert "never matched to fills" in rec["status"] and len(market["account"].queued) == 1


def test_a_market_order_is_credited_at_once(tmp_path, market):
    market["mtc"] = 8.0  # inside the MOC cutoff: a market order
    cfg = bucket(tmp_path, "paper", 100_000)
    rec = runner.run_once(cfg, live=True)
    assert rec["orders"][0]["order_type"] == "MKT"
    assert state(cfg)["positions"] == {"TQQQ": 2000} and state(cfg)["pending_orders"] == []


def test_reconcile_before_the_close_keeps_moc_orders_pending(tmp_path, market):
    cfg = bucket(tmp_path, "paper", 100_000)
    runner.run_once(cfg, live=True)
    rec = runner.reconcile(cfg)  # 20 minutes before the close: nothing has filled
    assert rec["status"] == "reconciled, 1 still pending" and state(cfg)["positions"] == {}


def test_a_short_fill_after_the_close_is_recorded(tmp_path, market):
    cfg = bucket(tmp_path, "paper", 100_000)
    runner.run_once(cfg, live=True)
    market["account"].queued[0].quantity = 1500  # only part of it filled
    rec = after_close(market, cfg)
    assert "filled short" in rec["status"] and state(cfg)["positions"] == {"TQQQ": 1500}


def test_reconcile_with_nothing_pending_does_not_connect(tmp_path, market, monkeypatch):
    monkeypatch.setattr(runner, "make_broker", lambda cfg: pytest.fail("connected"))
    assert runner.reconcile(bucket(tmp_path, "paper", 1000))["status"] == "nothing pending"


def test_legacy_bucket_is_seeded_from_the_account_once(tmp_path, market):
    cfg = bucket(tmp_path, "paper", 100_000)
    d = tmp_path / "paper"
    d.mkdir()
    (d / "state.json").write_text(json.dumps({"strategy_cash": 50_000.0, "equity_peak": 100_000.0}))
    with open(d / "trades.csv", "w", newline="") as f:
        csv.writer(f).writerows([["ts", "symbol", "result"], ["t", "TQQQ", "ibkr#9 Submitted"]])
    market["account"].positions["TQQQ"] = 1000
    rec = runner.run_once(cfg, live=True)
    assert rec["ledger_seeded"] == {"TQQQ": 1000} and rec["equity"] == pytest.approx(100_000)
    assert rec["status"] == "executed"  # 100% target: 2000 shares wanted, 1000 held


def test_dry_run_places_nothing_and_records_no_pending(tmp_path, market):
    cfg = bucket(tmp_path, "paper", 100_000)
    rec = runner.run_once(cfg, live=False)
    assert rec["status"] == "dry-run" and not market["account"].queued and not state(cfg).get("pending_orders")


# ---- gates ----


def test_kill_switch(tmp_path, market):
    cfg = bucket(tmp_path, "paper", 1000)
    (tmp_path / "paper").mkdir()
    (tmp_path / "paper" / "STOP").write_text("")
    assert runner.run_once(cfg, live=True)["status"].startswith("halted: kill switch")


def test_halted_state(tmp_path, market):
    cfg = bucket(tmp_path, "paper", 1000)
    (tmp_path / "paper").mkdir()
    (tmp_path / "paper" / "state.json").write_text(json.dumps({"halted": "drawdown breaker"}))
    assert runner.run_once(cfg, live=True)["status"].startswith("halted: drawdown breaker")


@pytest.mark.parametrize("mtc", [None, 3.0, 45.0])
def test_outside_the_near_close_window(tmp_path, market, mtc):
    market["mtc"] = mtc
    assert runner.run_once(bucket(tmp_path, "paper", 1000), live=True)["status"] == "skipped: outside near-close window"


def test_once_per_day(tmp_path, market):
    cfg = bucket(tmp_path, "paper", 100_000)
    assert runner.run_once(cfg, live=True)["status"] == "executed"
    assert runner.run_once(cfg, live=True)["status"] == "skipped: already completed today"
    assert len(market["account"].queued) == 1


def test_max_order_value(tmp_path, market):
    cfg = bucket(tmp_path, "paper", 100_000)
    cfg.risk.max_order_value = 50_000
    rec = runner.run_once(cfg, live=True)
    assert "exceeds max_order_value" in rec["status"] and not market["account"].queued


def test_drawdown_breaker_halts(tmp_path, market):
    cfg = bucket(tmp_path, "paper", 100_000)
    (tmp_path / "paper").mkdir()
    (tmp_path / "paper" / "state.json").write_text(
        json.dumps({"strategy_cash": 40_000.0, "positions": {}, "equity_peak": 100_000.0})
    )
    rec = runner.run_once(cfg, live=True)
    assert "drawdown breaker" in rec["status"] and state(cfg)["halted"] and not market["account"].queued


def test_live_profile_without_the_switch_is_a_dry_run(tmp_path, market):
    cfg = bucket(tmp_path, "live", 10_000)
    cfg.broker.live_trading_enabled = False
    assert runner.run_once(cfg, live=True)["status"] == "dry-run" and not market["account"].queued


def test_a_failed_run_with_working_orders_is_not_repeated(tmp_path, market, monkeypatch):
    cfg = bucket(tmp_path, "paper", 100_000)
    market["w_long"], market["account"].positions = 0.5, {}

    class Flaky(FakeBroker):
        def place(self, order):
            result = super().place(order)
            raise ConnectionError(f"lost the gateway after {result}")

    monkeypatch.setattr(runner, "make_broker", lambda cfg: Flaky(market["account"]))
    assert runner.run_once(cfg, live=True)["status"].startswith("error: lost the gateway")
    monkeypatch.setattr(runner, "make_broker", lambda cfg: FakeBroker(market["account"]))
    for force in (False, True):
        rec = runner.run_once(cfg, live=True, force=force)
        assert rec["status"] == "skipped: orders placed today are still waiting for fills"
    assert len(market["account"].queued) == 1
