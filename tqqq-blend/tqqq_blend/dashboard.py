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
# A data-source token in a URL (Finviz takes it as auth=...). Errors are recorded verbatim, so one quoting a
# request URL could otherwise carry the token onto the page.
_TOKEN = re.compile(r"(auth|token|apikey|api_key)=[^&\s\"']+", re.I)
WINDOW_MINUTES = 60  # a run within this many minutes of the close is a scheduled one


def scrub(value: Any) -> Any:
    """Recursively replace anything shaped like an IBKR account ID, and NaN/inf (which runs.jsonl can
    hold but JSON can't) with None."""
    if isinstance(value, str):
        return _TOKEN.sub(r"\1=[redacted]", _ACCOUNT_ID.sub("[account]", value))
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


def day_points(runs: list[dict]) -> dict[str, dict]:
    """One run per day for the charts: the last one that valued the account, preferring a scheduled near-close
    run, so a forced test run in the evening doesn't replace that day's close-time point."""
    best: dict[str, dict] = {}
    for r in runs:
        if _num(r.get("equity")) is None:
            continue
        day = r["ts"][:10]
        mtc = _num(r.get("minutes_to_close"))
        in_window = mtc is not None and 0 <= mtc <= WINDOW_MINUTES
        prev = best.get(day)
        if prev is None or in_window or not prev["_in_window"]:
            best[day] = r | {"_in_window": in_window}
    return {d: {k: v for k, v in r.items() if k != "_in_window"} for d, r in best.items()}


def sent(trades: list[dict]) -> int:
    """Orders actually sent (trades.csv also records the orders a dry run would have placed)."""
    return sum(1 for t in trades if t.get("result") and t["result"] != "DRY-RUN")


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
        "kind": "tqqq",
        "state_dir": str(state_dir.resolve()),
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

    # One point per day (day_points): the last run of the day that valued the account, near the close if any.
    by_day = day_points(runs)
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

    decision = next((r for r in reversed(runs) if _num(r.get("net")) is not None), None)
    trades = read_trades(state_dir)
    by_ts = {r["ts"]: r for r in runs}  # a trade carries its run's timestamp
    account |= _health(runs, now) | {
        "series": series,
        "metrics": metrics,
        "decision": decision,
        "trades": [trade_reason(t, by_ts) for t in reversed(trades[-RECENT_TRADES:])],
        "trades_sent": sent(trades),
        "trades_total": len(trades),
    }
    return scrub(account)


def _health(runs: list[dict], now: pd.Timestamp) -> dict:
    """The run-health fields every account card shows, from its run records."""
    run_dates = {datetime.fromisoformat(r["ts"]).date() for r in runs}
    recent_cutoff = str((now - pd.Timedelta(days=30)).date())
    return {
        "last_run": runs[-1],
        "due_through": str(due_through(now)),
        "missed_days": missed_days(run_dates, min(run_dates), due_through(now)),
        "errors_30d": sum(1 for r in runs if str(r.get("status", "")).startswith("error") and r["ts"] >= recent_cutoff),
        "runs": list(reversed(runs[-RECENT_RUNS:])),
    }


def summarize_bucket(state_dir: Path, now: pd.Timestamp) -> dict:
    """A bucket from another strategy (sleeves/), read from its state folder only: bucket.json describes it,
    runs.jsonl and trades.csv hold what it did. Its runs record equity, positions, prices and, at
    month-ends, the decision with its reasons (sleeves/runner.py)."""
    meta_path = state_dir / "bucket.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    runs, bad_lines = read_runs(state_dir)
    state = json.loads((state_dir / "state.json").read_text()) if (state_dir / "state.json").exists() else {}
    account: dict[str, Any] = {
        "kind": "holdings",
        "state_dir": str(state_dir.resolve()),
        "strategy": meta.get("strategy", state_dir.name),
        "name": meta.get("label") or state_dir.name,
        "bucket": meta.get("bucket"),
        "mode": meta.get("mode", "paper"),
        "capital": _num(meta.get("capital")),
        "halt_drawdown": _num(meta.get("halt_drawdown")),
        "rebalance_band": _num(meta.get("rebalance_band")),
        "symbols": {"benchmark": meta.get("benchmark", "SPY"), "cash": meta.get("cash_symbol", "SGOV")},
        "kill_switch": (state_dir / "STOP").exists(),
        "halted": state.get("halted"),
        "unreadable_run_lines": bad_lines,
        "reconcile": last_reconcile(state_dir),
        "runs_total": len(runs),
        "missing_meta": not meta,
    }
    if not runs:
        return scrub(account | {"series": [], "runs": [], "trades": [], "missed_days": [], "metrics": None})
    by_day = day_points(runs)
    series: list[dict[str, Any]] = [
        {"date": d, "equity": _num(r["equity"]), "peak": _num(r.get("peak")), "bench": _num(r.get("benchmark_price"))}
        for d, r in sorted(by_day.items())
    ]
    metrics = None
    if series:
        eq: list[float] = [p["equity"] for p in series]
        last_peak = max(max(eq), series[-1]["peak"] or 0)
        metrics = {
            "equity": eq[-1],
            "start_equity": eq[0],
            "start_date": series[0]["date"],
            "return": eq[-1] / eq[0] - 1 if eq[0] else None,
            "bench_return": _return(series, "bench"),
            "drawdown": eq[-1] / last_peak - 1 if last_peak else None,
            "max_drawdown": _max_drawdown(eq),
            "days": len(series),
        }
    valued = next((r for r in reversed(runs) if _num(r.get("equity")) is not None), None)
    holdings: list[dict[str, Any]] = []
    if valued:
        prices = valued.get("prices") or {}
        for sym, qty in sorted((valued.get("positions") or {}).items()):
            px = _num(prices.get(sym))
            holdings.append({"symbol": sym, "shares": qty, "price": px, "value": qty * px if px is not None else None})
        holdings.append({"symbol": "Cash", "shares": None, "price": None, "value": _num(valued.get("cash"))})
        total = _num(valued.get("equity"))
        for h in holdings:
            h["weight"] = h["value"] / total if total and h["value"] is not None else None
    trades = read_trades(state_dir)
    account |= _health(runs, now) | {
        "series": series,
        "metrics": metrics,
        "holdings": holdings,
        "holdings_ts": valued["ts"] if valued else None,
        "decision": next((r for r in reversed(runs) if r.get("why")), None),
        "next_check": runs[-1].get("schedule"),
        "trades": list(reversed(trades[-RECENT_TRADES:])),
        "trades_total": len(trades),
        "trades_sent": sent(trades),
    }
    return scrub(account)


def portfolio(name: str, members: list[dict]) -> dict:
    """Several buckets read as one portfolio: each day's value is the sum of every member's latest equity on
    or before that day (its capital before its first valued run). The benchmark is the first member that
    records one that day."""
    dates = sorted({p["date"] for m in members for p in m.get("series", [])})
    series = []
    for d in dates:
        total, bench = 0.0, None
        for m in members:
            pts = [p for p in m.get("series", []) if p["date"] <= d and p["equity"] is not None]
            total += pts[-1]["equity"] if pts else (m.get("capital") or 0.0)
        for m in members:
            same = [p for p in m.get("series", []) if p["date"] == d and p.get("bench") is not None]
            if same:
                bench = same[-1]["bench"]
                break
        series.append({"date": d, "equity": total, "bench": bench})
    capital = sum(m.get("capital") or 0.0 for m in members)
    rows = []
    for m in members:
        mm = m.get("metrics") or {}
        rows.append(
            {
                "name": m["name"],
                "id": m.get("_id"),
                "capital": m.get("capital"),
                "target": (m.get("capital") or 0.0) / capital if capital else None,
                "value": mm.get("equity", m.get("capital")),
                "return": mm.get("return"),
            }
        )
    total = sum(r["value"] or 0.0 for r in rows)
    for r in rows:
        r["weight"] = (r["value"] or 0.0) / total if total else None
    metrics = None
    if series:
        eq = [p["equity"] for p in series]
        metrics = {
            "equity": eq[-1],
            "capital": capital,
            "start_date": series[0]["date"],
            "return": eq[-1] / capital - 1 if capital else None,
            "bench_return": _return(series, "bench"),
            "drawdown": eq[-1] / max(eq) - 1 if max(eq) else None,
            "max_drawdown": _max_drawdown(eq),
        }
    return {"name": name, "series": series, "members": rows, "metrics": metrics}


def build(
    profiles: list[str],
    out: str | Path,
    now: pd.Timestamp | None = None,
    buckets: list[str] | None = None,
    portfolios: list[list[str]] | None = None,
) -> Path:
    """Write <out>/index.html for these config profiles, other strategies' bucket folders, and portfolios
    (each a name followed by its members' state folders), and return its path."""
    now = now if now is not None else pd.Timestamp.now(tz="America/New_York")
    strategies: list[dict[str, Any]] = [
        {"name": STRATEGY, "accounts": [summarize_profile(load_config(p), now) for p in profiles]}
    ]
    others: dict[str, list[dict]] = {}
    for b in buckets or []:
        acct = summarize_bucket(Path(b), now)
        others.setdefault(acct["strategy"], []).append(acct)
    strategies += [{"name": k, "accounts": v} for k, v in others.items()]
    for si, strat in enumerate(strategies):
        for ai, a in enumerate(strat["accounts"]):
            a["_id"] = f"acct-{si}-{ai}"
    by_dir = {a["state_dir"]: a for s in strategies for a in s["accounts"]}
    folios = []
    for spec in portfolios or []:
        name, dirs = spec[0], spec[1:]
        found = [by_dir.get(str(Path(d).resolve())) for d in dirs]
        folio = portfolio(name, [a for a in found if a is not None])
        folio["missing"] = [d for d, a in zip(dirs, found, strict=True) if a is None]  # named, never dropped
        folios.append(scrub(folio))
    for a in by_dir.values():  # server paths are only needed to match portfolio members; don't publish them
        a.pop("state_dir", None)
    data = {"generated": now.isoformat(timespec="seconds"), "portfolios": folios, "strategies": strategies}
    # No "<" at all inside the JSON (< decodes to the same text), so no recorded string can close the
    # <script> element or open a comment that changes how the browser parses it.
    payload = json.dumps(data, default=str, allow_nan=False).replace("<", "\\u003c")
    html = TEMPLATE.read_text(encoding="utf-8").replace("__DASHBOARD_DATA__", payload)
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "index.html"
    path.write_text(html, encoding="utf-8")
    return path
