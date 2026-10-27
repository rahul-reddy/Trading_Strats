"""Synthetic state files for the dashboard tests: runs.jsonl, trades.csv and state.json shaped like
runner.py writes them, with a failed run, a retried day and a day with no run at all."""

from __future__ import annotations

import csv
import json
import random
from pathlib import Path

import pandas as pd
import pandas_market_calendars as mcal

ROOT = Path(__file__).resolve().parents[1]
WHY_FROM = "2026-10-01"


def fake_why(net: float) -> dict:
    """A recorded explanation shaped like explain.explain() output (the wording is illustrative)."""
    trend = 1.0 if net > 0 else 0.0
    return {
        "version": 1,
        "sleeves": [
            {
                "key": "trend",
                "name": "Trend",
                "value": trend,
                "weight": 1 / 3,
                "rule": "Uptrend: long 100%" if trend else "Downtrend: no position",
                "detail": "QQQ is in an uptrend: above its 250-day average.",
            },
            {
                "key": "mr",
                "name": "Mean reversion",
                "value": 0.0,
                "weight": 1 / 3,
                "rule": "No setup",
                "detail": "RSI(2) is 48; it buys when RSI(2) drops below 10 while QQQ is in an uptrend.",
            },
            {
                "key": "vibha",
                "name": "Market timing",
                "value": 0.5,
                "weight": 1 / 3,
                "rule": "Holding 50%",
                "detail": "No new signal today; the position is unchanged since the retook the 50-day on Sep 30.",
                "flags": [],
            },
        ],
        "blend": [f"Weighted sleeves: ... = {net:+.2f}", "Volatility target: QQQ volatility is under 20%, no scaling"],
        "net": net,
    }


def trading_days(start: str, end: str) -> list[pd.Timestamp]:
    return [d.tz_localize(None) for d in mcal.get_calendar("NYSE").valid_days(start_date=start, end_date=end)]


def write_state(
    state_dir: Path,
    start: str = "2026-08-03",
    end: str = "2026-10-20",
    capital: float = 100_000.0,
    seed: int = 7,
    error_on: str | None = "2026-09-15",
    missed: str | None = "2026-09-22",
    retried: str | None = "2026-10-06",
) -> list[dict]:
    rng = random.Random(seed)
    state_dir.mkdir(parents=True, exist_ok=True)
    qqq, tqqq, sqqq = 560.0, 82.0, 21.0
    positions = {"TQQQ": 0, "SQQQ": 0}
    cash, peak = capital, capital
    runs: list[dict] = []
    trades: list[dict] = []
    net = 0.6
    for day in trading_days(start, end):
        r = rng.gauss(0.0006, 0.012)
        qqq *= 1 + r
        tqqq *= 1 + 3 * r
        sqqq *= 1 - 3 * r
        d = str(day.date())
        if d == missed:
            continue
        ts = f"{d}T15:30:0{rng.randint(1, 9)}"
        if d == error_on:
            runs.append(
                {
                    "ts": ts,
                    "broker": "ibkr",
                    "live": True,
                    "minutes_to_close": 29.9,
                    "quote_source": "finviz",
                    "status": "error: Stale data: last bar 2026-09-12 != today 2026-09-15 (account DU1234567)",
                }
            )
            continue
        if d == retried:
            runs.append({"ts": f"{d}T15:30:01", "broker": "ibkr", "live": True, "status": "error: connection refused"})
            ts = f"{d}T15:34:10"
        net = max(-0.5, min(1.0, net + rng.gauss(0, 0.18)))
        w_long, w_short = (net, 0.0) if net >= 0 else (0.0, -net)
        prices = {"TQQQ": round(tqqq, 2), "SQQQ": round(sqqq, 2)}
        held = positions["TQQQ"] * prices["TQQQ"] + positions["SQQQ"] * prices["SQQQ"]
        equity = cash + held
        peak = max(peak, equity)
        orders = []
        for sym, w in (("TQQQ", w_long), ("SQQQ", w_short)):
            target = int(w * equity // prices[sym])
            delta = target - positions[sym]
            cur_w = positions[sym] * prices[sym] / equity
            if delta and (abs(w - cur_w) > 0.1 or (w == 0) != (positions[sym] == 0)):
                side = "BUY" if delta > 0 else "SELL"
                orders.append({"symbol": sym, "side": side, "quantity": abs(delta), "order_type": "MOC"})
                cash -= delta * prices[sym]
                positions[sym] = target
                trades.append(
                    {
                        "ts": ts,
                        "broker": "ibkr",
                        "symbol": sym,
                        "side": side,
                        "qty": abs(delta),
                        "type": "MOC",
                        "ref_price": prices[sym],
                        "net_signal": round(net, 3),
                        "result": "Filled",
                    }
                )
        # runs from WHY_FROM on carry recorded reasons; earlier ones predate them
        extra = {"why": fake_why(round(net, 3))} if d >= WHY_FROM else {}
        runs.append(
            extra
            | {
                "ts": ts,
                "broker": "ibkr",
                "live": True,
                "minutes_to_close": 29.9,
                "quote_source": "finviz",
                "bar_date": d,
                "qqq": round(qqq, 2),
                "trend": round(min(1, max(-1, net + 0.2)), 3),
                "mr": round(rng.uniform(-0.3, 0.4), 3),
                "vibha": round(rng.choice([0.0, 0.5, 1.0]), 3),
                "vol_scale": round(rng.uniform(0.6, 1.0), 3),
                "net": round(net, 3),
                "dist_days": rng.randint(0, 5),
                "sell_flags": rng.randint(0, 2),
                "vibha_event": rng.choice(["", "", "", "follow-through day"]),
                "w_long": round(w_long, 3),
                "w_short": round(w_short, 3),
                "capital": equity,
                "equity": equity,
                "peak": peak,
                "positions": dict(positions),
                "prices": prices,
                "orders": orders,
                "status": "executed" if orders else "no change",
            }
        )
    with open(state_dir / "runs.jsonl", "w", encoding="utf-8") as f:
        for rec in runs:
            f.write(json.dumps(rec) + "\n")
    if trades:
        with open(state_dir / "trades.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(trades[0]))
            w.writeheader()
            w.writerows(trades)
    (state_dir / "state.json").write_text(
        json.dumps({"strategy_cash": cash, "equity_peak": peak, "last_completed_run": end})
    )
    return runs


def write_profile(path: Path, base: str, state_dir: Path, **broker: object) -> Path:
    """A profile TOML that extends one of the real ones but keeps its state in `state_dir`."""
    lines = [f'extends = "{(ROOT / base).as_posix()}"', f'state_dir = "{state_dir.as_posix()}"']
    if broker:
        lines.append("[broker]")
        lines += [f"{k} = {json.dumps(v)}" for k, v in broker.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_bucket(
    state_dir: Path,
    kind: str = "rotation",
    capital: float = 30_000.0,
    start: str = "2026-09-01",
    end: str = "2026-10-20",
    seed: int = 11,
) -> list[dict]:
    """State files shaped like sleeves/runner.py writes them: bucket.json, a daily valuation run, and a
    month-end decision with reasons and orders."""
    rng = random.Random(seed)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "bucket.json").write_text(
        json.dumps(
            {
                "strategy": "sleeves",
                "kind": kind,
                "name": "ETF rotation",
                "label": f"Paper · mix · {kind}",
                "bucket": f"sleeves/{kind}-paper",
                "account_group": "ibkr-paper",
                "mode": "paper",
                "capital": capital,
                "halt_drawdown": 0.30,
                "benchmark": "SPY",
                "cash_symbol": "SGOV",
                "rebalance_band": 0.02,
            }
        )
    )
    spy, gld = 700.0, 300.0
    positions: dict[str, int] = {}
    cash, peak = capital, capital
    runs, trades = [], []
    for day in trading_days(start, end):
        spy *= 1 + rng.gauss(0.0005, 0.01)
        gld *= 1 + rng.gauss(0.0003, 0.012)
        prices = {"SPY": spy, "GLD": gld, "SGOV": 100.0}
        ts = f"{day.date()}T15:34:10"
        equity = cash + sum(q * prices[s] for s, q in positions.items())
        peak = max(peak, equity)
        rec: dict = {"ts": ts, "bucket": f"sleeves/{kind}-paper", "kind": kind, "equity": equity, "peak": peak}
        rec |= {"cash": cash, "positions": dict(positions), "benchmark_price": spy, "prices": prices}
        rec |= {"schedule": "next month-end check on 2026-10-30", "orders": [], "status": "hold"}
        if str(day.date()) == "2026-09-30":
            orders = [{"symbol": "GLD", "side": "BUY", "quantity": 40, "order_type": "MOC"}]
            orders.append({"symbol": "SGOV", "side": "BUY", "quantity": 150, "order_type": "MOC"})
            rec |= {"schedule": "month-end check (2026-09-30)", "orders": orders, "status": "executed"}
            rec["why"] = {
                "summary": "Top 4 eligible: GLD; 3 slot(s) in T-bills",
                "decision": "rebalance to the targets",
                "reasons": [
                    {"symbol": "SGOV", "weight": 0.75, "text": "3 empty slot(s): T-bills"},
                    {"symbol": "GLD", "weight": 0.25, "text": "rank 1, score +8.0%: held at 25%"},
                    {"symbol": "TLT", "weight": 0.0, "text": "rank 2, score -1.0%: not eligible (below trend)"},
                ],
            }
            for o in orders:
                positions[o["symbol"]] = o["quantity"]
                cash -= o["quantity"] * prices[o["symbol"]]
                trades.append({"ts": ts, "broker": "ibkr", "symbol": o["symbol"], "side": "BUY", "qty": o["quantity"]})
                trades[-1] |= {"type": "MOC", "ref_price": prices[o["symbol"]], "result": "ibkr#1 Submitted"}
        runs.append(rec)
    with open(state_dir / "runs.jsonl", "w", encoding="utf-8") as f:
        for rec in runs:
            f.write(json.dumps(rec) + "\n")
    if trades:
        with open(state_dir / "trades.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(trades[0]))
            w.writeheader()
            w.writerows(trades)
    (state_dir / "state.json").write_text(json.dumps({"positions": positions, "strategy_cash": cash}))
    return runs
