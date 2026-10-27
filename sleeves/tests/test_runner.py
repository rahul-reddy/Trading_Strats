"""The sleeves runner end to end against a fake shared account: the clock, prices, history and the Finviz
screen are stubbed; the schedule, rules, ledger, gates and order planning are the real code."""

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from conftest import FakeBroker, closes, screen

from sleeves import runner
from sleeves.config import ROTATION_UNIVERSE, Config

OCT30, OCT29, NOV02 = date(2026, 10, 30), date(2026, 10, 29), date(2026, 11, 2)
UP = {"GLD": 0.003, "QQQ": 0.002, "DBC": 0.0025, "SPY": 0.0015}


@pytest.fixture
def market(monkeypatch, account):
    """Controls: the day, the minutes to the close, the drift of each symbol, a broken data source."""
    st = {"day": OCT30, "mtc": 20.0, "drift": UP, "account": account, "fail": None}

    def hist(symbols, today):
        if st["fail"] == "history":
            raise RuntimeError("stale history: ['SPY'] have no close for 2026-10-29")
        c = closes(sorted(set(symbols) | set(ROTATION_UNIVERSE)), end=str(today), drift=st["drift"])
        return c[c.index < pd.Timestamp(today)][list(symbols)]

    def prices(cfg, broker, symbols):
        c = closes(sorted(set(ROTATION_UNIVERSE) | {"SPY"}), end=str(st["day"]), drift=st["drift"])
        last = {s: float(c[s].iloc[-1]) for s in c} | {"SGOV": 100.0}
        last |= {f"Q{i:03d}": 50.0 for i in range(500)}
        account.prices |= last
        return {s: last[s] for s in symbols}, "finviz"

    def scr(token):
        if st["fail"] == "screen":
            raise RuntimeError("Finviz screen view 121 returned no rows (expired token?)")
        return screen()

    monkeypatch.setattr(runner, "et_today", lambda: st["day"])
    monkeypatch.setattr(runner, "minutes_to_close", lambda now=None: st["mtc"])
    monkeypatch.setattr(runner, "make_broker", lambda cfg: FakeBroker(account, st["day"]))
    monkeypatch.setattr(runner, "history", hist)
    monkeypatch.setattr(runner, "get_prices", prices)
    monkeypatch.setattr(runner, "sp500_screen", scr)
    monkeypatch.setattr(runner, "finviz_token", lambda env: "tok")
    return st


def bucket(tmp_path, kind="rotation", capital=30_000.0, name=None):
    cfg = Config()
    cfg.sleeve.kind = kind
    cfg.state_dir = str(tmp_path / (name or kind))
    cfg.bucket, cfg.account_group = f"sleeves/{name or kind}", "ibkr-paper"
    cfg.risk.capital, cfg.risk.max_order_value = capital, 1e9
    cfg.broker.name, cfg.broker.live_trading_enabled = "ibkr", True
    return cfg


def state(cfg):
    return json.loads((Path(cfg.state_dir) / "state.json").read_text())


def close_and_reconcile(market, cfg):
    market["account"].close(market["day"])
    market["mtc"] = -10.0
    rec = runner.reconcile(cfg)
    market["mtc"] = 20.0
    return rec


def test_a_new_bucket_values_itself_and_waits_for_month_end(tmp_path, market):
    market["day"] = OCT29
    rec = runner.run_once(bucket(tmp_path), live=True)
    assert rec["status"] == "hold" and rec["equity"] == 30_000 and rec["orders"] == []
    assert "first month-end check on 2026-10-30" in rec["schedule"]
    meta = json.loads((tmp_path / "rotation" / "bucket.json").read_text())
    assert meta["kind"] == "rotation" and meta["capital"] == 30_000 and meta["mode"] == "paper"


def test_month_end_rebalance_then_reconcile_credits_the_bucket(tmp_path, market):
    cfg = bucket(tmp_path)
    rec = runner.run_once(cfg, live=True)
    assert rec["status"] == "executed"
    assert {o["symbol"] for o in rec["orders"]} == {"GLD", "DBC", "QQQ", "SPY"}  # SPY trends up too
    assert all(o["order_type"] == "MOC" and o["ref"] == "sleeves/rotation" for o in rec["orders"])
    assert rec["targets"]["SPY"] == 0.25 and rec["why"]["reasons"]
    spent = sum(o["quantity"] * rec["prices"][o["symbol"]] for o in rec["orders"])
    assert spent <= 30_000 * 0.99  # 1% left in cash for MOC slippage
    assert close_and_reconcile(market, cfg)["status"] == "reconciled"
    s = state(cfg)
    assert set(s["positions"]) == {"GLD", "DBC", "QQQ", "SPY"} and s["last_rebalance"] == "2026-10-30"
    assert s["strategy_cash"] == pytest.approx(30_000 - spent - len(rec["orders"]) * 1.0)


def test_the_next_day_holds_and_values_only_its_own_shares(tmp_path, market):
    rot, other = bucket(tmp_path), bucket(tmp_path, capital=20_000, name="other")
    runner.run_once(rot, live=True)
    close_and_reconcile(market, rot)
    market["day"] = NOV02
    rec = runner.run_once(other, live=True)  # a second bucket in the same account
    assert rec["status"] == "hold" and rec["positions"] == {} and rec["equity"] == 20_000
    rec = runner.run_once(rot, live=True)
    assert rec["status"] == "hold" and set(rec["positions"]) == {"GLD", "DBC", "QQQ", "SPY"}


def test_a_missed_month_end_is_caught_up_next_day(tmp_path, market):
    cfg = bucket(tmp_path)
    market["day"] = OCT29
    runner.run_once(cfg, live=True)  # sets nothing: no check yet
    d = Path(cfg.state_dir)
    s = state(cfg) | {"last_check": "2026-09-30", "last_completed_run": "2026-10-29"}
    (d / "state.json").write_text(json.dumps(s))
    market["day"] = NOV02
    rec = runner.run_once(cfg, live=True)
    assert "catching up the 2026-10-30" in rec["schedule"] and rec["status"] == "executed"


def test_stale_history_stops_the_run(tmp_path, market):
    market["fail"] = "history"
    rec = runner.run_once(bucket(tmp_path), live=True)
    assert rec["status"].startswith("error: stale history") and not market["account"].queued
    assert "last_check" not in state(bucket(tmp_path))  # retried at the next firing


def test_quality_first_rebalance_then_holds_a_year(tmp_path, market):
    cfg = bucket(tmp_path, kind="quality", capital=25_000)
    rec = runner.run_once(cfg, live=True)
    assert rec["status"] == "executed" and len(rec["targets"]) == 25 and rec["decision"] == "first rebalance"
    close_and_reconcile(market, cfg)
    market["day"] = date(2026, 11, 30)
    rec = runner.run_once(cfg, live=True)
    assert rec["status"] == "no change" and "next annual rebalance" in rec["decision"] and rec["orders"] == []
    assert state(cfg)["last_check"] == "2026-11-30" and state(cfg)["last_rebalance"] == "2026-10-30"


def test_quality_moves_to_tbills_when_the_filter_turns_off(tmp_path, market):
    cfg = bucket(tmp_path, kind="quality", capital=25_000)
    runner.run_once(cfg, live=True)
    close_and_reconcile(market, cfg)
    market["day"], market["drift"] = date(2026, 11, 30), {"SPY": -0.004}
    rec = runner.run_once(cfg, live=True)
    assert rec["decision"] == "market filter turned off" and rec["targets"] == {"SGOV": 1.0}
    assert {o["side"] for o in rec["orders"] if o["symbol"] != "SGOV"} == {"SELL"}


def test_quality_without_its_screen_does_not_trade(tmp_path, market):
    market["fail"] = "screen"
    rec = runner.run_once(bucket(tmp_path, kind="quality"), live=True)
    assert "Finviz screen" in rec["status"] and not market["account"].queued


def test_an_unexplained_position_stops_the_run(tmp_path, market):
    market["account"].positions["GLD"] = 3  # a manual trade in a symbol this bucket trades
    rec = runner.run_once(bucket(tmp_path), live=True)
    assert "don't match the bucket ledgers" in rec["status"] and not market["account"].queued


def test_dry_run_without_the_live_switch(tmp_path, market):
    cfg = bucket(tmp_path)
    cfg.broker.live_trading_enabled = False
    rec = runner.run_once(cfg, live=True)
    assert rec["status"] == "dry-run" and rec["orders"] and not market["account"].queued
    assert "last_check" not in state(cfg)


def test_max_order_value(tmp_path, market):
    cfg = bucket(tmp_path)
    cfg.risk.max_order_value = 5_000
    assert "exceeds max_order_value" in runner.run_once(cfg, live=True)["status"]
    assert not market["account"].queued


def test_drawdown_breaker(tmp_path, market):
    cfg = bucket(tmp_path)
    Path(cfg.state_dir).mkdir(parents=True)
    (Path(cfg.state_dir) / "state.json").write_text(
        json.dumps({"positions": {}, "strategy_cash": 10_000.0, "equity_peak": 30_000.0})
    )
    rec = runner.run_once(cfg, live=True)
    assert "drawdown breaker" in rec["status"] and state(cfg)["halted"]


def test_gates(tmp_path, market):
    cfg = bucket(tmp_path)
    market["mtc"] = 45.0
    assert runner.run_once(cfg, live=True)["status"] == "skipped: outside near-close window"
    market["mtc"] = 20.0
    assert runner.run_once(cfg, live=True)["status"] == "executed"
    assert runner.run_once(cfg, live=True)["status"] == "skipped: already completed today"
    (Path(cfg.state_dir) / "STOP").write_text("")
    assert runner.run_once(cfg, live=True, force=True)["status"].startswith("halted: kill switch")


def test_a_failed_run_with_working_orders_is_not_repeated(tmp_path, market, monkeypatch):
    cfg = bucket(tmp_path)

    class Flaky(FakeBroker):
        def place(self, order):
            super().place(order)
            raise ConnectionError("lost the gateway")

    monkeypatch.setattr(runner, "make_broker", lambda c: Flaky(market["account"], market["day"]))
    assert runner.run_once(cfg, live=True)["status"].startswith("error: lost the gateway")
    monkeypatch.setattr(runner, "make_broker", lambda c: FakeBroker(market["account"], market["day"]))
    rec = runner.run_once(cfg, live=True, force=True)
    assert rec["status"] == "skipped: orders placed today are still waiting for fills"
    assert len(market["account"].queued) == 1


def test_plan_orders_band_and_whole_shares():
    orders = runner.plan_orders(
        {"A": 0.5, "B": 0.5}, {"A": 49, "C": 3}, {"A": 10.0, "B": 33.0, "C": 5.0}, 1000.0, 0.02, "MOC"
    )
    # A: 49% held vs 50% target is inside the band; C is closed; B is opened with whole shares
    assert [(o.symbol, o.side, o.quantity) for o in orders] == [("C", "SELL", 3), ("B", "BUY", 15)]
    with pytest.raises(RuntimeError, match="no usable price"):
        runner.plan_orders({"A": 1.0}, {}, {"A": float("nan")}, 1000.0, 0.02, "MOC")


def test_a_run_that_cannot_connect_is_recorded(tmp_path, market, monkeypatch):
    class Down(FakeBroker):
        def connect(self):
            raise ConnectionError("gateway not logged in")

    monkeypatch.setattr(runner, "make_broker", lambda c: Down(market["account"], market["day"]))
    cfg = bucket(tmp_path)
    assert runner.run_once(cfg, live=True)["status"] == "error: gateway not logged in"
    last = (Path(cfg.state_dir) / "runs.jsonl").read_text().splitlines()[-1]
    assert json.loads(last)["status"] == "error: gateway not logged in"


def test_a_broker_without_fill_reports_sends_nothing(tmp_path, market, monkeypatch):
    class NoFills(FakeBroker):
        reports_fills = False

    monkeypatch.setattr(runner, "make_broker", lambda c: NoFills(market["account"], market["day"]))
    rec = runner.run_once(bucket(tmp_path), live=True)
    assert "can't report fills" in rec["status"] and not market["account"].queued
