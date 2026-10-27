"""Daily dashboard: one self-contained HTML page built from each profile's state files.

Reads only what the runs already record (state/<profile>/runs.jsonl, trades.csv, state.json, STOP), so it
never fetches data and never guesses: a value the runs didn't record is shown as missing. Benchmarks (QQQ
and TQQQ buy-and-hold) come from the prices each run recorded, over the same days as the strategy.

    python -m tqqq_blend dashboard --profiles config.ibkr-paper.toml config.ibkr-live.toml --out state/dashboard

The page embeds its data as JSON, so it opens from disk and needs no server. On AWS the
trading-dashboard timer builds it after the close and publishes it behind a password
(deploy/server/publish-dashboard.sh).
"""

from __future__ import annotations

import csv
import json
import math
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pandas_market_calendars as mcal

from .config import Config, load_config

STRATEGY = "tqqq-blend"
TEMPLATE = Path(__file__).with_name("dashboard.html")
RUN_DEADLINE = (15, 45)  # the near-close run is done by 15:45 ET; before that, today isn't due yet
RECENT_RUNS = 30
RECENT_TRADES = 50
# IBKR account IDs (U1234567, DU1234567, DF...) can appear in broker error messages. The page is
# password-protected, but account numbers never belong on it.
_ACCOUNT_ID = re.compile(r"\b(?:D[A-Z]?|U)\d{5,}\b")


def scrub(value: Any) -> Any:
    """Recursively replace anything shaped like an IBKR account ID, and NaN/inf (which runs.jsonl can
    hold but JSON can't) with None."""
    if isinstance(value, str):
        return _ACCOUNT_ID.sub("[account]", value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def read_runs(state_dir: Path) -> tuple[list[dict], int]:
    """Every run record, oldest first, and how many lines could not be parsed (shown, never hidden)."""
    path = state_dir / "runs.jsonl"
    if not path.exists():
        return [], 0
    runs, bad = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            datetime.fromisoformat(rec["ts"])
        except (ValueError, KeyError, TypeError):
            bad += 1
            continue
        runs.append(rec)
    return sorted(runs, key=lambda r: r["ts"]), bad


def last_reconcile(state_dir: Path) -> dict | None:
    """The latest post-close reconcile record (runner.reconcile), or None if there is none or it can't be read."""
    path = state_dir / "reconcile.jsonl"
    if not path.exists():
        return None
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        try:
            rec = json.loads(line)
        except ValueError:
            return {"ts": None, "status": "error: the last line of reconcile.jsonl can't be read"}
        if isinstance(rec, dict):
            return rec
    return None


def read_trades(state_dir: Path) -> list[dict]:
    path = state_dir / "trades.csv"
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _num(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def due_through(now: pd.Timestamp) -> date | None:
    """The latest trading day whose run should already exist at `now` (ET)."""
    now = now.tz_convert("America/New_York")
    days = mcal.get_calendar("NYSE").valid_days(start_date=now.date() - pd.Timedelta(days=10), end_date=now.date())
    dates = [d.date() for d in days]
    if dates and dates[-1] == now.date() and (now.hour, now.minute) < RUN_DEADLINE:
        dates.pop()
    return dates[-1] if dates else None


def missed_days(run_dates: set[date], first: date, through: date | None) -> list[str]:
    """Trading days from the first run through `through` with no run record at all."""
    if through is None or through < first:
        return []
    days = mcal.get_calendar("NYSE").valid_days(start_date=first, end_date=through)
    return [str(d.date()) for d in days if d.date() not in run_dates]


def _max_drawdown(values: list[float]) -> float:
    peak, worst = values[0], 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:  # equity at or below zero has no meaningful drawdown ratio; don't divide by it
            worst = min(worst, v / peak - 1)
    return worst


def _return(series: list[dict], key: str) -> float | None:
    """Return over the series for `key`, using the first and last points that recorded it."""
    vals = [p[key] for p in series if p[key] is not None]
    return vals[-1] / vals[0] - 1 if len(vals) >= 2 and vals[0] else None


def trade_reason(trade: dict, by_ts: dict[str, dict]) -> dict:
    """The trade plus why its run wanted that exposure: each sleeve's rule and the resulting target.
    Runs from before reasons were recorded give only the target; the page says so."""
    run = by_ts.get(trade.get("ts", ""))
    if run is None:
        return trade
    why = run.get("why") or {}
    return trade | {
        "why": {
            "rules": [f"{s['name']}: {s['rule']}" for s in why.get("sleeves", [])],
            "net": _num(run.get("net")),
            "w_long": _num(run.get("w_long")),
            "w_short": _num(run.get("w_short")),
        }
    }


def summarize_profile(cfg: Config, now: pd.Timestamp) -> dict:
    """Everything the page shows for one account profile."""
    state_dir = Path(cfg.state_dir)
    b, s = cfg.broker, cfg.strategy
    paper = b.name == "paper" or b.ibkr_account_type == "paper"
    runs, bad_lines = read_runs(state_dir)
    state = json.loads((state_dir / "state.json").read_text()) if (state_dir / "state.json").exists() else {}
    account: dict[str, Any] = {
        "name": cfg.label or ("Paper" if paper else "Live"),
        "bucket": cfg.bucket,
        "broker": b.name,
        "mode": "paper" if paper else ("live" if b.live_trading_enabled else "live-disabled"),
        "capital": cfg.risk.capital,
        "halt_drawdown": cfg.risk.max_drawdown_halt,
        "rebalance_band": s.rebalance_band,
        "symbols": {"signal": s.signal_symbol, "long": s.long_symbol, "short": s.short_symbol},
        "kill_switch": (state_dir / "STOP").exists(),
        "halted": state.get("halted"),
        "unreadable_run_lines": bad_lines,
        "reconcile": last_reconcile(state_dir),
        "runs_total": len(runs),
    }
    if not runs:
        return scrub(account | {"series": [], "runs": [], "trades": [], "missed_days": [], "metrics": None})

    # One point per day: the last run of the day that got as far as valuing the account.
    by_day: dict[str, dict] = {}
    for r in runs:
        if _num(r.get("equity")) is not None:
            by_day[r["ts"][:10]] = r
    series: list[dict[str, Any]] = [
        {
            "date": d,
            "equity": _num(r["equity"]),
            "peak": _num(r.get("peak")),
            "qqq": _num(r.get("qqq")),
            "long_px": _num((r.get("prices") or {}).get(s.long_symbol)),
            "net": _num(r.get("net")),
            "w_long": _num(r.get("w_long")),
            "w_short": _num(r.get("w_short")),
        }
        for d, r in sorted(by_day.items())
    ]

    metrics = None
    if series:
        equities: list[float] = [p["equity"] for p in series]
        last_peak = max(max(equities), series[-1]["peak"] or 0)
        metrics = {
            "equity": equities[-1],
            "start_equity": equities[0],
            "start_date": series[0]["date"],
            "return": equities[-1] / equities[0] - 1 if equities[0] else None,
            "qqq_return": _return(series, "qqq"),
            "long_return": _return(series, "long_px"),
            "drawdown": equities[-1] / last_peak - 1 if last_peak else None,
            "max_drawdown": _max_drawdown(equities),
            "days": len(series),
        }

    run_dates = {datetime.fromisoformat(r["ts"]).date() for r in runs}
    decision = next((r for r in reversed(runs) if _num(r.get("net")) is not None), None)
    recent_cutoff = str((now - pd.Timedelta(days=30)).date())
    trades = read_trades(state_dir)
    by_ts = {r["ts"]: r for r in runs}  # a trade carries its run's timestamp
    account |= {
        "series": series,
        "metrics": metrics,
        "last_run": runs[-1],
        "decision": decision,
        "due_through": str(due_through(now)),
        "missed_days": missed_days(run_dates, min(run_dates), due_through(now)),
        "errors_30d": sum(1 for r in runs if str(r.get("status", "")).startswith("error") and r["ts"] >= recent_cutoff),
        "runs": list(reversed(runs[-RECENT_RUNS:])),
        "trades": [trade_reason(t, by_ts) for t in reversed(trades[-RECENT_TRADES:])],
        "trades_total": len(trades),
    }
    return scrub(account)


def build(profiles: list[str], out: str | Path, now: pd.Timestamp | None = None) -> Path:
    """Write <out>/index.html for these config profiles and return its path."""
    now = now if now is not None else pd.Timestamp.now(tz="America/New_York")
    data = {
        "generated": now.isoformat(timespec="seconds"),
        "strategies": [
            {"name": STRATEGY, "accounts": [summarize_profile(load_config(p), now) for p in profiles]},
        ],
    }
    # No "<" at all inside the JSON (< decodes to the same text), so no recorded string can close the
    # <script> element or open a comment that changes how the browser parses it.
    payload = json.dumps(data, default=str, allow_nan=False).replace("<", "\\u003c")
    html = TEMPLATE.read_text(encoding="utf-8").replace("__DASHBOARD_DATA__", payload)
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "index.html"
    path.write_text(html, encoding="utf-8")
    return path
