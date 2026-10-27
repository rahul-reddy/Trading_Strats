"""The daily near-close process.

1. Gate: trading day? within the [min, max] minutes-to-close window? kill switch absent?
2. Signal: QQQ history + live price -> blended target weights for TQQQ / SQQQ.
3. Risk: data sanity, drawdown breaker, order-size cap.
4. Ledger: this bucket's own shares and cash (ledger.py), checked against the whole account.
5. Plan: diff target shares vs the bucket's shares, skip changes inside the rebalance band.
6. Execute (only with --live AND broker.live_trading_enabled): sells first, then buys, tagged with the bucket.
7. Log: state/trades.csv, state/runs.jsonl, state/state.json (ledger, equity peak).

After the close, `reconcile` credits the day's fills to the bucket (see reconcile()).
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
import pandas_market_calendars as mcal

from . import ledger
from .brokers import Order, make_broker
from .brokers.ibkr import IBKRBroker
from .config import Config
from .data import live_signal_bars
from .explain import explain
from .quotes import get_prices
from .strategy import compute_signals

log = logging.getLogger("tqqq_blend")
MOC_CUTOFF_MIN = 10  # Nasdaq MOC entry cutoff is 15:50 ET (10 min before a 16:00 close)


def minutes_to_close(now_utc: pd.Timestamp | None = None) -> float | None:
    """Minutes until today's NYSE close, or None if the market isn't open today."""
    now_utc = now_utc or pd.Timestamp.now(tz="UTC")
    sched = mcal.get_calendar("NYSE").schedule(start_date=now_utc.date(), end_date=now_utc.date())
    if sched.empty:
        return None
    close = sched.iloc[0]["market_close"]
    open_ = sched.iloc[0]["market_open"]
    if now_utc < open_:
        return None
    return (close - now_utc).total_seconds() / 60


def plan_orders(
    targets: dict[str, float],
    positions: dict[str, int],
    prices: dict[str, float],
    capital: float,
    band: float,
    order_type: str,
) -> list[Order]:
    """Turn target weights into whole-share orders. Sells come first so buys have cash."""
    orders = []
    for sym, w in targets.items():
        cur = positions.get(sym, 0)
        cur_w = cur * prices[sym] / capital
        tgt_sh = int(w * capital // prices[sym])
        moving_to_or_from_zero = (w == 0) != (cur == 0)
        if abs(w - cur_w) <= band and not moving_to_or_from_zero:
            continue
        delta = tgt_sh - cur
        if delta:
            orders.append(Order(sym, "BUY" if delta > 0 else "SELL", abs(delta), order_type))
    return sorted(orders, key=lambda o: o.side != "SELL")


class StateStore:
    def __init__(self, state_dir: str):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "state.json"
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {}

    def save(self):
        # Atomic: other buckets on the account read this file (ledger.check_account) while this one runs,
        # and a half-written file would stop their runs.
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=2, default=str))
        tmp.replace(self.path)

    def append_run(self, rec: dict):
        with open(self.dir / "runs.jsonl", "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")

    def append_reconcile(self, rec: dict):
        with open(self.dir / "reconcile.jsonl", "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")

    def append_trade(self, rec: dict, name: str = "trades.csv"):
        p = self.dir / name
        new = not p.exists()
        with open(p, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rec))
            if new:
                w.writeheader()
            w.writerow(rec)


def run_once(cfg: Config, live: bool = False, force: bool = False) -> dict:
    s, risk = cfg.strategy, cfg.risk
    store = StateStore(cfg.state_dir)
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), "broker": cfg.broker.name, "live": live}

    if (Path(cfg.state_dir) / "STOP").exists():
        rec["status"] = "halted: kill switch file state/STOP exists"
        store.append_run(rec)
        return rec
    if store.data.get("halted"):
        rec["status"] = f"halted: {store.data['halted']} (clear 'halted' in state/state.json to resume)"
        store.append_run(rec)
        return rec

    mtc = minutes_to_close()
    rec["minutes_to_close"] = mtc
    if not force and (mtc is None or not risk.min_minutes_to_close <= mtc <= risk.max_minutes_to_close):
        rec["status"] = "skipped: outside near-close window"
        return rec
    # MOC orders don't show up as positions until the close, so a second run the same day would
    # re-send them. Only the first successful run of the day acts; failed runs can retry.
    today_str = str(pd.Timestamp.now(tz="America/New_York").date())
    if not force and store.data.get("last_completed_run") == today_str:
        rec["status"] = "skipped: already completed today"
        return rec
    # A run that failed after placing some orders hasn't completed, but its MOC orders are working and not
    # yet in the ledger: placing again would double them. Even --force doesn't.
    if any(p["date"] == today_str for p in store.data.get("pending_orders", [])):
        rec["status"] = "skipped: orders placed today are still waiting for fills"
        store.append_run(rec)
        return rec

    broker = make_broker(cfg)
    broker.connect()
    try:
        quotes, rec["quote_source"] = get_prices(cfg, broker, (s.signal_symbol, s.long_symbol, s.short_symbol))
        qqq_px = quotes[s.signal_symbol]
        bars = live_signal_bars(s.signal_symbol, qqq_px, risk.volume_projection)
        sig = compute_signals(bars, s)
        last = sig.iloc[-1]
        rec.update(
            {
                "bar_date": str(sig.index[-1].date()),
                "qqq": qqq_px,
                "trend": last["trend"],
                "mr": last["mr"],
                "vibha": last["vibha"],
                "vol_scale": last["vol_scale"],
                "net": last["net"],
                "dist_days": int(last["dist_days"]),
                "sell_flags": int(last["sell_flags"]),
                "vibha_event": last["vibha_event"],
                "w_long": last["w_long"],
                "w_short": last["w_short"],
            }
        )
        # Plain-language reasons for the dashboard. Informational only: a failure here is recorded and
        # must never stop or change the run, so it can't be what decides whether an order goes out.
        try:
            rec["why"] = explain(sig, s)
        except Exception as e:  # noqa: BLE001
            rec["why_error"] = f"{type(e).__name__}: {e}"

        # ---- risk checks ----
        today = pd.Timestamp.now(tz="America/New_York").tz_localize(None).normalize()
        if sig.index[-1] != today and not force:
            raise RuntimeError(f"Stale data: last bar {sig.index[-1].date()} != today {today.date()}")
        day_move = abs(sig["close"].iloc[-1] / sig["close"].iloc[-2] - 1)
        if day_move > risk.max_signal_move:
            raise RuntimeError(f"QQQ moved {day_move:.1%} today (> {risk.max_signal_move:.0%}); review manually")

        prices = {sym: quotes[sym] for sym in (s.long_symbol, s.short_symbol)}
        # The bucket owns only its ledger's shares and cash (ledger.py), never the account's positions:
        # other buckets may trade the same symbols in the same account.
        positions = bucket_positions(cfg, store, broker, set(prices), rec)
        held_value = sum(positions.get(k, 0) * prices[k] for k in prices)
        netliq = broker.net_liquidation()
        strat_equity = store.data["strategy_cash"] + held_value
        capital = min(strat_equity, netliq)
        peak = max(store.data.get("equity_peak", strat_equity), strat_equity)
        store.data["equity_peak"] = peak
        if strat_equity < peak * (1 - risk.max_drawdown_halt):
            store.data["halted"] = f"drawdown breaker {strat_equity:.0f} vs peak {peak:.0f}"
            store.save()
            raise RuntimeError(store.data["halted"])

        targets = {s.long_symbol: float(last["w_long"]), s.short_symbol: float(last["w_short"])}
        use_moc = (cfg.broker.name == "ibkr" and cfg.broker.ibkr_use_moc) or cfg.broker.name == "schwab"
        otype = "MOC" if use_moc and mtc is not None and mtc > MOC_CUTOFF_MIN + 1 else "MKT"
        orders = plan_orders(targets, positions, prices, capital, s.rebalance_band, otype)
        for o in orders:
            o.ref = cfg.bucket
            if o.quantity * prices[o.symbol] > risk.max_order_value:
                raise RuntimeError(f"Order {o} exceeds max_order_value {risk.max_order_value}")

        rec.update(
            {
                "capital": capital,
                "equity": strat_equity,
                "peak": peak,
                "positions": positions,
                "prices": prices,
                "orders": [vars(o) for o in orders],
            }
        )
        armed = cfg.broker.name == "paper" or (live and cfg.broker.live_trading_enabled)
        for o in orders:
            if armed:  # pending before it is sent: if sending fails half way, the order may still be working
                ledger.record_pending(store.data, [o], today.date(), ["sending"])
                store.save()
            result = broker.place(o) if armed else "DRY-RUN"
            if armed:
                store.data["pending_orders"][-1]["result"] = result
                store.save()
            store.append_trade(
                {
                    "ts": rec["ts"],
                    "broker": cfg.broker.name,
                    "symbol": o.symbol,
                    "side": o.side,
                    "qty": o.quantity,
                    "type": o.order_type,
                    "ref_price": prices[o.symbol],
                    "net_signal": round(float(last["net"]), 3),
                    "result": result,
                }
            )
            log.info("%s %s %d %s -> %s", o.side, o.symbol, o.quantity, o.order_type, result)
        # Market orders (and the local paper broker) fill at once: credit them now. MOC orders fill at the
        # close and are credited by the post-close reconcile.
        if armed and any(o.order_type == "MKT" for o in orders) or (armed and orders and cfg.broker.name == "paper"):
            settle_fills(store, broker.fills(cfg.bucket), today.date(), after_close=False)
        rec["status"] = ("executed" if armed else "dry-run") if orders else "no change"
        if armed:
            store.data["last_completed_run"] = today_str
    except Exception as e:  # noqa: BLE001 - every failure must be recorded and must stop trading
        rec["status"] = f"error: {e}"
        log.exception("run failed")
    finally:
        broker.disconnect()
        store.save()
        store.append_run(rec)
    return rec


def bucket_positions(cfg: Config, store: StateStore, broker, symbols: set[str], rec: dict) -> dict[str, int]:
    """This bucket's shares, after checking that every bucket's ledger on the account adds up to what the
    account holds. A bucket from before ledgers is seeded once from the account; a new one starts with
    risk.capital in cash and no shares. Raises ledger.LedgerError instead of trading on doubtful shares."""
    data = store.data
    if not getattr(broker, "reports_fills", False):
        raise ledger.LedgerError(f"{broker.name} can't report fills, so this bucket's shares can't be tracked")
    data["bucket"], data["account_group"] = cfg.bucket, cfg.account_group
    today = pd.Timestamp.now(tz="America/New_York").date()
    stale = ledger.stale_pending(data, today)
    if stale:
        raise ledger.LedgerError(
            f"orders from an earlier day were never matched to fills ({stale}); IB reports only the current "
            "day's executions, so check them in IBKR and fix the ledger (docs/BUCKETS.md)"
        )
    account = broker.positions()
    if "positions" not in data:
        if "strategy_cash" in data:
            ledger.seed_from_broker(Path(cfg.state_dir), data, account, symbols)
            rec["ledger_seeded"] = dict(data["positions"])
        else:
            data["positions"], data["strategy_cash"] = {}, cfg.risk.capital
    ledger.check_account(Path(cfg.state_dir), cfg.account_group, account, symbols, data, today, cfg.risk.account_check)
    return ledger.positions(data)


def settle_fills(store: StateStore, fills, today, after_close: bool) -> dict:
    """Apply the bucket's new fills to its ledger and drop pending orders that are finished."""
    new = ledger.apply_fills(store.data, fills)
    done = ledger.settle_pending(store.data, fills, today, after_close)
    for f in new:
        store.append_trade(vars(f), "fills.csv")
    store.save()
    short = [d for d in done if abs(d["filled"]) != d["qty"]]
    return {"fills": len(new), "settled": len(done), "short_fills": short}


def reconcile(cfg: Config) -> dict:
    """After the close: credit today's fills for this bucket to its ledger. IB reports only the current
    day's executions, so this runs before the server stops (deploy/server/trading-reconcile.timer)."""
    store = StateStore(cfg.state_dir)
    rec: dict = {"ts": datetime.now().isoformat(timespec="seconds"), "bucket": cfg.bucket}
    if not store.data.get("pending_orders"):
        rec["status"] = "nothing pending"
        store.append_reconcile(rec)
        return rec
    mtc = minutes_to_close()
    after_close = mtc is not None and mtc < 0
    today = pd.Timestamp.now(tz="America/New_York").date()
    broker = make_broker(cfg)
    try:  # a failed connection is recorded too, so the dashboard shows it the same evening
        if isinstance(broker, IBKRBroker):
            broker.connect(readonly=True, client_id=cfg.broker.ibkr_client_id + 200)
        else:
            broker.connect()
        rec |= settle_fills(store, broker.fills(cfg.bucket), today, after_close)
        left = store.data.get("pending_orders", [])
        rec["pending"] = len(left)
        rec["status"] = "reconciled" + (f", {len(left)} still pending" if left else "")
        if rec["short_fills"]:
            rec["status"] += f", {len(rec['short_fills'])} order(s) filled short"
    except Exception as e:  # noqa: BLE001 - recorded; the next run refuses to trade on unmatched orders
        rec["status"] = f"error: {e}"
        log.exception("reconcile failed")
    finally:
        broker.disconnect()
        store.append_reconcile(rec)
    return rec
