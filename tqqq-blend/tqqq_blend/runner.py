"""The daily near-close process.

  1. Gate: trading day? within the [min, max] minutes-to-close window? kill switch absent?
  2. Signal: QQQ history + live price -> blended target weights for TQQQ / SQQQ.
  3. Risk: data sanity, drawdown breaker, order-size cap.
  4. Plan: diff target shares vs broker positions, skip changes inside the rebalance band.
  5. Execute (only with --live AND broker.live_trading_enabled): sells first, then buys.
  6. Log: state/trades.csv, state/runs.jsonl, state/state.json (equity peak).
"""
from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
import pandas_market_calendars as mcal

from .brokers import Order, make_broker
from .config import Config
from .data import live_signal_bars
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


def plan_orders(targets: dict[str, float], positions: dict[str, int], prices: dict[str, float],
                capital: float, band: float, order_type: str) -> list[Order]:
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
        self.path.write_text(json.dumps(self.data, indent=2, default=str))

    def append_run(self, rec: dict):
        with open(self.dir / "runs.jsonl", "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")

    def append_trade(self, rec: dict):
        p = self.dir / "trades.csv"
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

    broker = make_broker(cfg)
    broker.connect()
    try:
        qqq_px = broker.last_price(s.signal_symbol)
        bars = live_signal_bars(s.signal_symbol, qqq_px, risk.volume_projection)
        sig = compute_signals(bars, s)
        last = sig.iloc[-1]
        rec.update({
            "bar_date": str(sig.index[-1].date()), "qqq": qqq_px,
            "trend": last["trend"], "mr": last["mr"], "vibha": last["vibha"],
            "vol_scale": last["vol_scale"], "net": last["net"],
            "dist_days": int(last["dist_days"]), "sell_flags": int(last["sell_flags"]),
            "vibha_event": last["vibha_event"], "w_long": last["w_long"], "w_short": last["w_short"],
        })

        # ---- risk checks ----
        today = pd.Timestamp.now(tz="America/New_York").tz_localize(None).normalize()
        if sig.index[-1] != today and not force:
            raise RuntimeError(f"Stale data: last bar {sig.index[-1].date()} != today {today.date()}")
        day_move = abs(sig["close"].iloc[-1] / sig["close"].iloc[-2] - 1)
        if day_move > risk.max_signal_move:
            raise RuntimeError(f"QQQ moved {day_move:.1%} today (> {risk.max_signal_move:.0%}); review manually")

        prices = {sym: broker.last_price(sym) for sym in (s.long_symbol, s.short_symbol)}
        positions = broker.positions()
        held_value = sum(positions.get(k, 0) * prices[k] for k in prices)
        netliq = broker.net_liquidation()
        # The strategy keeps its own cash ledger so it can share an account with other holdings:
        # equity = ledger cash + TQQQ/SQQQ value, seeded from risk.capital on the first run.
        cash = store.data.setdefault("strategy_cash", risk.capital - held_value)
        strat_equity = cash + held_value
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
            if o.quantity * prices[o.symbol] > risk.max_order_value:
                raise RuntimeError(f"Order {o} exceeds max_order_value {risk.max_order_value}")

        rec.update({"capital": capital, "equity": strat_equity, "peak": peak, "positions": positions, "prices": prices,
                    "orders": [vars(o) for o in orders]})
        armed = cfg.broker.name == "paper" or (live and cfg.broker.live_trading_enabled)
        for o in orders:
            result = broker.place(o) if armed else "DRY-RUN"
            if armed:  # ledger uses the reference price; MOC slippage makes it a close approximation
                store.data["strategy_cash"] -= o.signed_qty * prices[o.symbol]
            store.append_trade({"ts": rec["ts"], "broker": cfg.broker.name, "symbol": o.symbol, "side": o.side,
                                "qty": o.quantity, "type": o.order_type, "ref_price": prices[o.symbol],
                                "net_signal": round(float(last["net"]), 3), "result": result})
            log.info("%s %s %d %s -> %s", o.side, o.symbol, o.quantity, o.order_type, result)
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
