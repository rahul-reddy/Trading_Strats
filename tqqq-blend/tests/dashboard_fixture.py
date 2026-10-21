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
        runs.append(
            {
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
