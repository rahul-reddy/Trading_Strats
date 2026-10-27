"""The daily near-close process for one bucket.

1. Gates: trading day, inside the [min, max] minutes-to-close window, no kill switch, not halted, not
   already done today, no orders from today still waiting for fills.
2. Value: the bucket's own shares (ledger.py) at live prices; drawdown breaker.
3. Decide, only at a month-end check (schedule.py): the sleeve's rules (rules.py) give target weights.
4. Ledger: the account must hold what every bucket's ledger on it adds up to (docs/BUCKETS.md).
5. Plan: whole shares toward the targets, sells first; an existing holding is resized only beyond the band.
6. Execute (only with --live AND broker.live_trading_enabled), tagged with the bucket; log everything.

After the close, `reconcile` credits the day's fills to the bucket's ledger.
"""

from __future__ import annotations

import csv
import json
import logging
import math
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from . import ledger, rules, schedule
from .brokers import Order, make_broker
from .brokers.ibkr import IBKRBroker
from .config import Config
from .data import finviz_token, get_prices, history, sp500_screen, with_live

log = logging.getLogger("sleeves")
MOC_CUTOFF_MIN = 10  # NYSE MOC entry cutoff is 15:50 ET
NAMES = {"rotation": "ETF rotation", "sectors": "Sector momentum", "quality": "Quality-value"}


def minutes_to_close(now_utc: pd.Timestamp | None = None) -> float | None:
    """Minutes until today's NYSE close, or None if the market isn't open today (or hasn't opened yet)."""
    import pandas_market_calendars as mcal

    now_utc = now_utc or pd.Timestamp.now(tz="UTC")
    sched = mcal.get_calendar("NYSE").schedule(start_date=now_utc.date(), end_date=now_utc.date())
    if sched.empty or now_utc < sched.iloc[0]["market_open"]:
        return None
    return (sched.iloc[0]["market_close"] - now_utc).total_seconds() / 60


def et_today() -> date:
    return pd.Timestamp.now(tz="America/New_York").date()


class StateStore:
    def __init__(self, state_dir: str):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "state.json"
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {}

    def save(self):
        # Atomic: other buckets on the account read this file (ledger.check_account) while this one runs.
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=2, default=str))
        tmp.replace(self.path)

    def append(self, name: str, rec: dict):
        with open(self.dir / name, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")

    def append_csv(self, name: str, rec: dict):
        p = self.dir / name
        new = not p.exists()
        with open(p, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rec))
            if new:
                w.writeheader()
            w.writerow(rec)


def write_bucket_meta(cfg: Config) -> None:
    """bucket.json: what the dashboard (tqqq-blend/dashboard.py) needs to show this bucket."""
    b = cfg.broker
    paper = b.name == "paper" or b.ibkr_account_type == "paper"
    meta = {
        "strategy": "sleeves",
        "kind": cfg.sleeve.kind,
        "name": NAMES[cfg.sleeve.kind],
        "label": cfg.label or NAMES[cfg.sleeve.kind],
        "bucket": cfg.bucket,
        "account_group": cfg.account_group,
        "mode": "paper" if paper else ("live" if b.live_trading_enabled else "live-disabled"),
        "capital": cfg.risk.capital,
        "halt_drawdown": cfg.risk.max_drawdown_halt,
        "benchmark": cfg.sleeve.benchmark,
        "cash_symbol": cfg.sleeve.cash_symbol,
        "rebalance_band": cfg.sleeve.rebalance_band,
    }
    d = Path(cfg.state_dir)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "bucket.json.tmp"
    tmp.write_text(json.dumps(meta, indent=2))
    tmp.replace(d / "bucket.json")


def plan_orders(
    targets: dict[str, float],
    held: dict[str, int],
    prices: dict[str, float],
    capital: float,
    band: float,
    order_type: str,
) -> list[Order]:
    """Whole-share orders toward `targets` (fractions of `capital`). A symbol held and still wanted is resized
    only if its weight is off by more than `band`; opening and closing positions always trade. Sells first."""
    orders = []
    for sym in sorted(set(targets) | set(held)):
        w, cur = targets.get(sym, 0.0), held.get(sym, 0)
        price = prices[sym]
        if not (price > 0 and math.isfinite(price)):
            raise RuntimeError(f"no usable price for {sym}")
        if w and cur and abs(w - cur * price / capital) <= band:
            continue
        delta = int(w * capital // price) - cur
        if delta:
            orders.append(Order(sym, "BUY" if delta > 0 else "SELL", abs(delta), order_type))
    return sorted(orders, key=lambda o: (o.side != "SELL", o.symbol))


def decide(cfg: Config, broker, store: StateStore, today: date, prices: dict[str, float], rec: dict):
    """The month-end decision: (target weights or None to hold, why). Fetches what the rules need and
    raises on missing or stale data."""
    sc = cfg.sleeve
    need = sorted(set(sc.symbols()) | {sc.filter_symbol, sc.benchmark})
    closes = with_live(history(need, today), {s: prices[s] for s in need}, today)
    move = abs(closes[sc.benchmark].iloc[-1] / closes[sc.benchmark].iloc[-2] - 1)
    if move > cfg.risk.max_signal_move:
        raise RuntimeError(f"{sc.benchmark} moved {move:.1%} today (> {cfg.risk.max_signal_move:.0%}); review manually")
    if sc.kind == "rotation":
        d = rules.rotation(closes, sc)
    elif sc.kind == "sectors":
        d = rules.sectors(closes, sc)
    else:
        screen = sp500_screen(finviz_token(cfg.broker.finviz_token_env))
        d = rules.quality(screen, closes, sc)
        filter_on = sc.cash_symbol not in d.weights
        trade, why = schedule.quality_trade_due(store.data, today, filter_on, sc.q_rebalance_months)
        store.data["filter_on"] = filter_on
        rec["why"] = {"summary": d.summary, "reasons": d.reasons, "decision": why}
        return (d.weights if trade else None), why
    rec["why"] = {"summary": d.summary, "reasons": d.reasons, "decision": "rebalance to the targets"}
    return d.weights, "rebalance to the targets"


def run_once(cfg: Config, live: bool = False, force: bool = False, check_now: bool = False) -> dict:
    """One near-close run. --force skips the time window; check_now makes today a month-end check (both for
    testing; a dry run unless --live and live_trading_enabled)."""
    sc, risk = cfg.sleeve, cfg.risk
    store = StateStore(cfg.state_dir)
    write_bucket_meta(cfg)
    rec: dict = {"ts": datetime.now().isoformat(timespec="seconds"), "broker": cfg.broker.name, "live": live}
    rec |= {"bucket": cfg.bucket, "kind": sc.kind}
    today = et_today()
    today_str = str(today)

    def finish(status: str) -> dict:
        rec["status"] = status
        store.append("runs.jsonl", rec)
        return rec

    if (Path(cfg.state_dir) / "STOP").exists():
        return finish("halted: kill switch file STOP exists")
    if store.data.get("halted"):
        return finish(f"halted: {store.data['halted']} (clear 'halted' in state.json to resume)")
    mtc = minutes_to_close()
    rec["minutes_to_close"] = mtc
    if not force and (mtc is None or not risk.min_minutes_to_close <= mtc <= risk.max_minutes_to_close):
        rec["status"] = "skipped: outside near-close window"
        return rec
    if not force and store.data.get("last_completed_run") == today_str:
        rec["status"] = "skipped: already completed today"
        return rec
    if any(p["date"] == today_str for p in store.data.get("pending_orders", [])):
        return finish("skipped: orders placed today are still waiting for fills")

    broker = make_broker(cfg)
    try:  # a failed connection is a recorded error too, so the dashboard shows it
        broker.connect()
        if not getattr(broker, "reports_fills", False):
            raise ledger.LedgerError(f"{broker.name} can't report fills, so this bucket's shares can't be tracked")
        data = store.data
        data["bucket"], data["account_group"] = cfg.bucket, cfg.account_group
        if "positions" not in data:  # a new bucket: its capital in cash, no shares
            data["positions"], data["strategy_cash"] = {}, risk.capital
        stale = ledger.stale_pending(data, today)
        if stale:
            raise ledger.LedgerError(
                f"orders from an earlier day were never matched to fills ({stale}); IB reports only the current "
                "day's executions, so check them in IBKR and fix the ledger (docs/BUCKETS.md)"
            )
        held = ledger.positions(data)
        base = sorted(set(held) | {sc.benchmark, sc.cash_symbol, sc.filter_symbol} | set(sc.symbols()))
        prices, rec["quote_source"] = get_prices(cfg, broker, base)
        equity = data["strategy_cash"] + sum(q * prices[s] for s, q in held.items())
        peak = max(data.get("equity_peak", equity), equity)
        data["equity_peak"] = peak
        rec |= {"equity": equity, "peak": peak, "cash": data["strategy_cash"], "positions": held}
        rec["benchmark_price"] = prices[sc.benchmark]
        if equity < peak * (1 - risk.max_drawdown_halt):
            data["halted"] = f"drawdown breaker {equity:.0f} vs peak {peak:.0f}"
            store.save()
            raise RuntimeError(data["halted"])

        due, due_why = (True, "check requested (--check-now)") if check_now else schedule.check_due(data, today)
        rec["schedule"] = due_why
        targets = None
        if due:
            targets, rec["decision"] = decide(cfg, broker, store, today, prices, rec)
        if targets is not None:
            extra = sorted(set(targets) - set(prices))
            if extra:
                more, src = get_prices(cfg, broker, extra)
                prices |= more
                rec["quote_source"] = rec["quote_source"] if src == rec["quote_source"] else "mixed"
        ledger.check_account(
            Path(cfg.state_dir),
            cfg.account_group,
            broker.positions(),
            set(held) | set(targets or {}),
            data,
            today,
            risk.account_check,
        )
        rec["prices"] = {s: prices[s] for s in sorted(set(held) | set(targets or {}) | {sc.benchmark})}
        orders: list[Order] = []
        if targets is not None:
            capital = min(equity, broker.net_liquidation()) * sc.invest_fraction
            otype = "MOC" if cfg.broker.ibkr_use_moc and mtc is not None and mtc > MOC_CUTOFF_MIN + 1 else "MKT"
            orders = plan_orders(targets, held, prices, capital, sc.rebalance_band, otype)
            for o in orders:
                o.ref = cfg.bucket
                if o.quantity * prices[o.symbol] > risk.max_order_value:
                    raise RuntimeError(f"Order {o} exceeds max_order_value {risk.max_order_value}")
            rec["targets"] = targets
        rec["orders"] = [vars(o) for o in orders]
        armed = cfg.broker.name == "paper" or (live and cfg.broker.live_trading_enabled)
        for o in orders:
            if armed:  # pending before it is sent: if sending fails half way, the order may still be working
                ledger.record_pending(data, [o], today, ["sending"])
                store.save()
            result = broker.place(o) if armed else "DRY-RUN"
            if armed:
                data["pending_orders"][-1]["result"] = result
                store.save()
            store.append_csv(
                "trades.csv",
                {
                    "ts": rec["ts"],
                    "broker": cfg.broker.name,
                    "symbol": o.symbol,
                    "side": o.side,
                    "qty": o.quantity,
                    "type": o.order_type,
                    "ref_price": prices[o.symbol],
                    "result": result,
                },
            )
            log.info("%s %s %d %s -> %s", o.side, o.symbol, o.quantity, o.order_type, result)
        # Market orders (and the local paper broker) fill at once: credit them now. MOC orders fill at the
        # close and are credited by the post-close reconcile.
        if armed and any(o.order_type == "MKT" for o in orders) or (armed and orders and cfg.broker.name == "paper"):
            settle_fills(store, broker.fills(cfg.bucket), today, after_close=False)
        if armed:
            data["last_completed_run"] = today_str
            if due:
                data["last_check"] = today_str
            if targets is not None:
                data["last_rebalance"] = today_str
        status = ("executed" if armed else "dry-run") if orders else ("no change" if due else "hold")
        rec["status"] = status
    except Exception as e:  # noqa: BLE001 - every failure must be recorded and must stop trading
        rec["status"] = f"error: {e}"
        log.exception("run failed")
    finally:
        broker.disconnect()
        store.save()
        store.append("runs.jsonl", rec)
    return rec


def settle_fills(store: StateStore, fills, today, after_close: bool) -> dict:
    new = ledger.apply_fills(store.data, fills)
    done = ledger.settle_pending(store.data, fills, today, after_close)
    for f in new:
        store.append_csv("fills.csv", vars(f))
    store.save()
    short = [d for d in done if abs(d["filled"]) != d["qty"]]
    return {"fills": len(new), "settled": len(done), "short_fills": short}


def reconcile(cfg: Config) -> dict:
    """After the close: credit today's fills for this bucket to its ledger (IB reports only today's)."""
    store = StateStore(cfg.state_dir)
    rec: dict = {"ts": datetime.now().isoformat(timespec="seconds"), "bucket": cfg.bucket}
    if not store.data.get("pending_orders"):
        rec["status"] = "nothing pending"
        store.append("reconcile.jsonl", rec)
        return rec
    mtc = minutes_to_close()
    broker = make_broker(cfg)
    try:  # a failed connection is recorded too, so the dashboard shows it the same evening
        if isinstance(broker, IBKRBroker):
            broker.connect(readonly=True, client_id=cfg.broker.ibkr_client_id + 200)
        else:
            broker.connect()
        rec |= settle_fills(store, broker.fills(cfg.bucket), et_today(), after_close=mtc is not None and mtc < 0)
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
        store.append("reconcile.jsonl", rec)
    return rec
