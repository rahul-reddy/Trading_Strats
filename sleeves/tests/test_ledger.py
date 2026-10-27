"""A copy of tqqq-blend/tests/test_ledger.py: sleeves/ledger.py is a copy of tqqq_blend/ledger.py."""

import json

import pytest

from sleeves import ledger
from sleeves.brokers import Fill, Order

TODAY = "2026-10-27"


def fill(exec_id, symbol, shares, price=10.0, ref="a", commission=1.0, day=TODAY):
    return Fill(exec_id, ref, symbol, shares, price, commission, f"{day}T16:00:00-04:00")


def write(root, name, data):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(data))
    return d


def test_apply_fills_once_per_execution_and_only_this_buckets():
    data = {"bucket": "a", "strategy_cash": 1000.0, "positions": {}}
    fs = [fill("e1", "TQQQ", 10), fill("e2", "TQQQ", -4, price=12.0), fill("x", "TQQQ", 99, ref="b")]
    assert [f.exec_id for f in ledger.apply_fills(data, fs)] == ["e1", "e2"]
    assert ledger.apply_fills(data, fs) == []  # a second reconcile of the same day changes nothing
    assert data["positions"] == {"TQQQ": 6}
    assert data["strategy_cash"] == pytest.approx(1000 - 100 - 1 + 48 - 1)


def test_selling_everything_removes_the_symbol():
    data = {"bucket": "a", "strategy_cash": 0.0, "positions": {"SQQQ": 5}}
    ledger.apply_fills(data, [fill("e1", "SQQQ", -5)])
    assert data["positions"] == {}


def test_settle_pending_needs_the_full_fill_until_the_close():
    data = {"pending_orders": [{"date": TODAY, "symbol": "TQQQ", "side": "BUY", "qty": 10, "result": "r"}]}
    partial = [fill("e1", "TQQQ", 6)]
    assert ledger.settle_pending(data, partial, TODAY, after_close=False) == []
    done = ledger.settle_pending(data, partial, TODAY, after_close=True)
    assert done[0]["filled"] == 6 and data["pending_orders"] == []


def test_full_fill_settles_before_the_close():
    data = {"pending_orders": [{"date": TODAY, "symbol": "TQQQ", "side": "SELL", "qty": 3, "result": "r"}]}
    assert ledger.settle_pending(data, [fill("e1", "TQQQ", -3)], TODAY, after_close=False)[0]["filled"] == -3


def test_an_earlier_days_order_is_never_settled_from_todays_fills():
    old = {"date": "2026-10-26", "symbol": "TQQQ", "side": "BUY", "qty": 10, "result": "r"}
    data = {"pending_orders": [old]}
    assert ledger.settle_pending(data, [], TODAY, after_close=True) == []
    assert ledger.stale_pending(data, TODAY) == [old]


def test_account_must_equal_the_sum_of_bucket_ledgers(tmp_path):
    a = write(tmp_path, "a", {"account_group": "g", "positions": {"TQQQ": 10}})
    write(tmp_path, "b", {"account_group": "g", "positions": {"TQQQ": 5, "SPY": 2}})
    write(tmp_path, "other", {"account_group": "elsewhere", "positions": {"TQQQ": 100}})
    mine = json.loads((a / "state.json").read_text())
    ledger.check_account(a, "g", {"TQQQ": 15, "SPY": 2, "AAPL": 7}, {"TQQQ", "SQQQ"}, mine, TODAY)  # AAPL: unmanaged
    with pytest.raises(ledger.LedgerError, match="TQQQ: account holds 16"):
        ledger.check_account(a, "g", {"TQQQ": 16, "SPY": 2}, {"TQQQ"}, mine, TODAY)
    with pytest.raises(ledger.LedgerError, match="SQQQ: account holds 3"):  # a symbol this bucket trades
        ledger.check_account(a, "g", {"TQQQ": 15, "SPY": 2, "SQQQ": 3}, {"TQQQ", "SQQQ"}, mine, TODAY)


def test_orders_in_flight_may_already_have_filled(tmp_path):
    a = write(tmp_path, "a", {"account_group": "g", "positions": {}})
    pend = {"date": TODAY, "symbol": "TQQQ", "side": "BUY", "qty": 10, "result": "r"}
    write(tmp_path, "b", {"account_group": "g", "positions": {"TQQQ": 5}, "pending_orders": [pend]})
    mine = {"account_group": "g", "positions": {}}
    for held in (5, 15):  # before and after b's market order filled
        ledger.check_account(a, "g", {"TQQQ": held}, {"TQQQ"}, mine, TODAY)
    with pytest.raises(ledger.LedgerError):
        ledger.check_account(a, "g", {"TQQQ": 16}, {"TQQQ"}, mine, TODAY)


def test_at_least_allows_the_owners_own_shares_on_top(tmp_path):
    a = write(tmp_path, "a", {"account_group": "g", "positions": {"TQQQ": 10}})
    mine = {"account_group": "g", "positions": {"TQQQ": 10}}
    ledger.check_account(a, "g", {"TQQQ": 50}, {"TQQQ"}, mine, TODAY, mode="at_least")
    with pytest.raises(ledger.LedgerError):  # but never less than the bucket owns
        ledger.check_account(a, "g", {"TQQQ": 9}, {"TQQQ"}, mine, TODAY, mode="at_least")
    with pytest.raises(ValueError):
        ledger.check_account(a, "g", {}, set(), mine, TODAY, mode="loose")


def test_a_sibling_without_a_ledger_stops_the_run(tmp_path):
    a = write(tmp_path, "a", {"account_group": "g", "positions": {}})
    write(tmp_path, "old", {"account_group": "g", "strategy_cash": 5.0})
    with pytest.raises(ledger.LedgerError, match="no share ledger"):
        ledger.check_account(a, "g", {}, {"TQQQ"}, {"account_group": "g", "positions": {}}, TODAY)


def test_unreadable_sibling_stops_the_run(tmp_path):
    a = write(tmp_path, "a", {"account_group": "g", "positions": {}})
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "state.json").write_text("{not json")
    with pytest.raises(ledger.LedgerError, match="can't read"):
        ledger.check_account(a, "g", {}, set(), {"account_group": "g", "positions": {}}, TODAY)


def test_seed_from_broker_takes_own_symbols_once_it_has_traded(tmp_path):
    a = write(tmp_path, "a", {"account_group": "g", "strategy_cash": 5.0})
    (a / "trades.csv").write_text("ts,symbol,result\nt,TQQQ,ibkr#1 Submitted\n")
    data = {"account_group": "g", "bucket": "a", "strategy_cash": 5.0}
    ledger.seed_from_broker(a, data, {"TQQQ": 40, "AAPL": 3}, {"TQQQ", "SQQQ"})
    assert data["positions"] == {"TQQQ": 40}


def test_seed_refuses_when_another_bucket_holds_the_symbol(tmp_path):
    a = write(tmp_path, "a", {"account_group": "g", "strategy_cash": 5.0})
    (a / "trades.csv").write_text("ts,symbol,result\nt,TQQQ,ibkr#1 Submitted\n")
    write(tmp_path, "b", {"account_group": "g", "positions": {"TQQQ": 2}})
    with pytest.raises(ledger.LedgerError, match="already hold"):
        ledger.seed_from_broker(a, {"account_group": "g", "bucket": "a"}, {"TQQQ": 42}, {"TQQQ"})


def test_a_bucket_that_only_dry_ran_owns_nothing(tmp_path):
    a = write(tmp_path, "a", {"account_group": "g", "strategy_cash": 5.0})
    (a / "trades.csv").write_text("ts,symbol,result\nt,TQQQ,DRY-RUN\n")
    data = {"account_group": "g", "bucket": "a"}
    ledger.seed_from_broker(a, data, {"TQQQ": 40}, {"TQQQ"})  # the owner's own shares
    assert data["positions"] == {}


def test_record_pending_keeps_order_details():
    data: dict = {}
    ledger.record_pending(data, [Order("TQQQ", "BUY", 3, "MOC", "a")], TODAY, ["ibkr#1"])  # type: ignore[arg-type]
    assert data["pending_orders"] == [{"date": TODAY, "symbol": "TQQQ", "side": "BUY", "qty": 3, "result": "ibkr#1"}]


def test_an_old_pending_order_on_a_sibling_explains_nothing(tmp_path):
    a = write(tmp_path, "a", {"account_group": "g", "positions": {}})
    old = {"date": "2026-10-26", "symbol": "TQQQ", "side": "BUY", "qty": 10, "result": "r"}
    write(tmp_path, "b", {"account_group": "g", "positions": {"TQQQ": 5}, "pending_orders": [old]})
    mine = {"account_group": "g", "positions": {}}
    ledger.check_account(a, "g", {"TQQQ": 5}, {"TQQQ"}, mine, TODAY)
    with pytest.raises(ledger.LedgerError):  # it may have filled yesterday, but nobody knows
        ledger.check_account(a, "g", {"TQQQ": 15}, {"TQQQ"}, mine, TODAY)
