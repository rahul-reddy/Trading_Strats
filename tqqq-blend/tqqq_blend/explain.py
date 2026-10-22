"""Plain-language reasons for a day's signal: which rule set each sleeve, and how they blend into the target.

explain(sig, c) reads one row of compute_signals' output (the last, by default) and returns a JSON-ready
dict. It mirrors the rules in strategy.py without changing them; tests check that the blend it describes
reproduces the recorded net exposure on every day of a long synthetic series. The runner records it with
each run (runs.jsonl "why") and the dashboard shows it under each decision and trade.

Path-dependent sleeves (the mean-reversion hold and the timing state machine) are explained from the
recorded sleeve values: the day a position was entered is the start of the current run of equal values.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from .config import StrategyConfig

VERSION = 1


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _pct(x: float | None, signed: bool = False) -> str:
    if x is None:
        return "n/a"
    return f"{x:+.1%}" if signed else f"{x:.0%}"


def _date(sig: pd.DataFrame, i: int) -> str:
    t = pd.Timestamp(sig.index[i])
    return f"{t:%b} {t.day}"


def _run_start(values: list[float], i: int) -> int:
    """First index of the run of equal values that ends at i."""
    j = i
    while j > 0 and values[j - 1] == values[i]:
        j -= 1
    return j


def _trend(sig: pd.DataFrame, c: StrategyConfig, i: int) -> dict:
    r = sig.iloc[i]
    v, close, slow, fast = _f(r["trend"]), _f(r["close"]), _f(r["sma_slow"]), _f(r["sma_fast"])
    ext, bbu = _f(r["ext"]), _f(r.get("bb_upper"))
    out: dict[str, Any] = {"key": "trend", "name": "Trend", "value": v, "weight": c.weight_trend}
    if v is None or slow is None or fast is None or close is None:
        return out | {"rule": "Not enough history yet", "detail": f"Needs {c.trend_slow} days of QQQ closes."}
    if v > 0:
        steps = [
            f"QQQ is in an uptrend: above its {c.trend_slow}-day average"
            if close >= slow
            else f"QQQ is still in its uptrend: within {_pct(c.trend_hysteresis)} below its {c.trend_slow}-day "
            "average, which doesn't end it"
        ]
        size = 1.0
        if ext is not None and ext > c.ext_tier2:
            size = c.ext_size2
            steps.append(
                f"{_pct(ext)} above that average (over {_pct(c.ext_tier2)}), so size steps down to {_pct(size)}"
            )
        elif ext is not None and ext > c.ext_tier1:
            size = c.ext_size1
            steps.append(
                f"{_pct(ext)} above that average (over {_pct(c.ext_tier1)}), so size steps down to {_pct(size)}"
            )
        if close < fast:
            size = min(size, c.below_fast_size)
            steps.append(f"but it closed below its {c.trend_fast}-day average, a weakening trend: at most {_pct(size)}")
        if bbu is not None and close > bbu:
            steps.append("and it closed above the upper Bollinger band (a momentum phase): full size")
        rule = "Uptrend: long " + _pct(v)
        return out | {"rule": rule, "detail": "; ".join(steps) + "."}
    if v < 0:
        return out | {
            "rule": "Downtrend: short",
            "detail": f"QQQ is below its {c.trend_slow}-day average and below a falling {c.trend_fast}-day average.",
        }
    if close < slow * (1 - c.trend_hysteresis):
        detail = f"QQQ is below its {c.trend_slow}-day average"
        detail += (
            ", and trend shorts are switched off." if not c.short_enabled else ", but not in a confirmed downtrend."
        )
        return out | {"rule": "Downtrend: no position", "detail": detail}
    return out | {
        "rule": "No trend position",
        "detail": f"QQQ is within {_pct(c.trend_hysteresis)} of its {c.trend_slow}-day average and has not "
        "reclaimed it since the last downtrend, so the sleeve waits.",
    }


def _mr(sig: pd.DataFrame, c: StrategyConfig, i: int) -> dict:
    r = sig.iloc[i]
    v, rsi_now, close, slow = _f(r["mr"]), _f(r["rsi_mr"]), _f(r["close"]), _f(r["sma_slow"])
    out: dict[str, Any] = {"key": "mr", "name": "Mean reversion", "value": v, "weight": c.weight_mr}
    if v is None or rsi_now is None or slow is None or close is None:
        return out | {"rule": "Not enough history yet", "detail": f"Needs {c.trend_slow} days of QQQ closes."}
    rsi_txt = f"RSI({c.mr_rsi_len}) is {rsi_now:.0f}"
    mr = [float(x) for x in sig["mr"].to_numpy()]
    if v != 0:
        e = _run_start(mr, i)
        er = sig.iloc[e]
        ersi = _f(er["rsi_mr"])
        held = i - e
        when = "today" if held == 0 else f"on {_date(sig, e)} ({held} day{'s' if held != 1 else ''} ago)"
        if v > 0 and v != c.mr_deep_size:
            rule = "Buy the dip"
            entry = f"RSI({c.mr_rsi_len}) fell to {ersi:.0f} (below {c.mr_long_entry:.0f}) in an uptrend {when}"
            exit_ = f"It sells when RSI({c.mr_rsi_len}) rises above {c.mr_long_exit:.0f} or after {c.mr_max_hold} days"
        elif v > 0:
            rule = "Oversold bounce"
            entry = (
                f"QQQ was over {_pct(-c.mr_deep_ext)} below its {c.trend_slow}-day average and RSI({c.mr_rsi_len}) "
                f"fell to {ersi:.0f} (below {c.mr_deep_entry:.0f}) {when}"
            )
            exit_ = f"It sells when RSI({c.mr_rsi_len}) rises above {c.mr_long_exit:.0f} or after {c.mr_max_hold} days"
        else:
            rule = "Fade the rally"
            entry = f"RSI({c.mr_rsi_len}) rose to {ersi:.0f} (above {c.mr_short_entry:.0f}) in a downtrend {when}"
            exit_ = (
                f"It covers when RSI({c.mr_rsi_len}) falls below {c.mr_short_exit:.0f}, after {c.mr_max_hold} days, "
                f"or if QQQ closes above its {c.trend_slow}-day average"
            )
        return out | {"rule": rule, "detail": f"{entry}. {exit_}; today {rsi_txt}."}
    prev = mr[i - 1] if i > 0 else 0.0
    if prev != 0:
        e = _run_start(mr, i - 1)
        held = i - e
        if prev > 0:
            why = (
                f"{rsi_txt}, above {c.mr_long_exit:.0f}: the bounce is done"
                if rsi_now > c.mr_long_exit
                else f"the {c.mr_max_hold}-day holding limit was reached"
            )
        elif rsi_now < c.mr_short_exit:
            why = f"{rsi_txt}, below {c.mr_short_exit:.0f}: the fade is done"
        elif close > slow:
            why = f"QQQ closed back above its {c.trend_slow}-day average"
        else:
            why = f"the {c.mr_max_hold}-day holding limit was reached"
        return out | {"rule": "Closed the trade", "detail": f"After {held} days, {why}."}
    if close > slow:
        need = f"it buys when RSI({c.mr_rsi_len}) drops below {c.mr_long_entry:.0f} while QQQ is in an uptrend"
    else:
        need = (
            f"below the {c.trend_slow}-day it fades rallies when RSI({c.mr_rsi_len}) tops {c.mr_short_entry:.0f}, "
            f"and buys a small bounce when RSI({c.mr_rsi_len}) drops below {c.mr_deep_entry:.0f} with QQQ "
            f"over {_pct(-c.mr_deep_ext)} under that average"
        )
    return out | {"rule": "No setup", "detail": f"{rsi_txt}; {need}."}


_EVENTS = {
    "FTD": "Follow-through day",
    "HHHL": "Higher highs and lows",
    "reclaim50": "Retook the 50-day",
    "exit<21ema": "Exit below the 21-day EMA",
}


# how a past event reads in "since ... on <date>"
_SINCE = {
    "FTD": "the follow-through day",
    "HHHL": "the higher-highs-and-lows entry",
    "reclaim50": "the retake of the 50-day average",
    "exit<21ema": "the exit below the 21-day EMA",
}


def _event_text(ev: str, c: StrategyConfig) -> str:
    if ev == "FTD":
        return (
            f"follow-through day: QQQ rose at least {_pct(c.ftd_min_gain)} on higher volume on day "
            f"{c.ftd_min_day}+ of a rally attempt, so the sleeve goes to 100%"
        )
    if ev == "HHHL":
        return (
            f"{c.hhhl_days} days of higher highs and higher lows with an up close: a pilot position "
            f"({_pct(c.hhhl_base_size)}, plus 25% each for closing above the 21-day EMA and the {c.reclaim_ma}-day)"
        )
    if ev == "reclaim50":
        return f"QQQ closed back above its {c.reclaim_ma}-day average: {_pct(c.reclaim_size)}"
    if ev == "exit<21ema":
        return f"QQQ closed below its {c.exit_ma}-day EMA: full exit"
    if ev.startswith("trim("):
        n = ev[5:].split("flags")[0]
        return f"{n} sell flags are active and a new one fired: trimmed {_pct(c.trim_fraction)}"
    if ev.startswith("distcap("):
        n = ev[8:].rstrip(")")
        return f"{n} distribution days: capped at {_pct(c.dist_heavy_cap)}"
    return ev


def _flags(sig: pd.DataFrame, c: StrategyConfig, i: int) -> list[str]:
    """The individual sell flags that are active on day i (strategy.vibha_sleeve counts them)."""
    if i < 3:
        return []
    cl, h, lo, v = (sig[k].to_numpy() for k in ("close", "high", "low", "volume"))
    hi52, vavg, sma_s = _f(sig["hi_52w"].iloc[i]), _f(sig["vol_avg"].iloc[i]), _f(sig["sma_short"].iloc[i])
    nd = int(sig["dist_days"].iloc[i])
    out = []
    if hi52 is not None and vavg is not None and cl[i] >= hi52 and v[i] < vavg:
        out.append("new 52-week high on below-average volume")
    if nd >= c.dist_warn:
        out.append(f"{nd} distribution days in the last {c.dist_window}")
    three_down = cl[i] < cl[i - 1] < cl[i - 2] < cl[i - 3]
    if three_down:
        out.append("three down days in a row")
    if sma_s is not None and cl[i] < sma_s and v[i] > v[i - 1]:
        out.append(f"closed below the {c.short_ma}-day average on higher volume")
    if three_down and v[i] > v[i - 1] > v[i - 2] and h[i] < h[i - 1] < h[i - 2] and lo[i] < lo[i - 1] < lo[i - 2]:
        out.append("three down days on rising volume with lower highs and lows")
    return out


def _vibha(sig: pd.DataFrame, c: StrategyConfig, i: int) -> dict:
    r = sig.iloc[i]
    v = _f(r["vibha"])
    out: dict[str, Any] = {"key": "vibha", "name": "Market timing", "value": v, "weight": c.weight_vibha}
    if v is None:
        return out | {"rule": "Not enough history yet", "detail": ""}
    flags = _flags(sig, c, i)
    below_slow = _f(r["sma_slow"]) is not None and r["close"] < r["sma_slow"]
    capped = below_slow and v == c.bear_regime_cap
    cap_txt = (
        f" Below the {c.trend_slow}-day average, timing positions are capped at {_pct(c.bear_regime_cap)}."
        if capped
        else ""
    )
    flag_txt = (" Sell flags: " + "; ".join(flags) + ".") if flags else ""
    today = [e for e in str(r["vibha_event"] or "").split(",") if e]
    if today:
        rule = " + ".join(_EVENTS.get(e, "Trim" if e.startswith("trim") else "Distribution cap") for e in today)
        detail = "Today: " + "; ".join(_event_text(e, c) for e in today) + "."
        return out | {"rule": rule, "detail": detail + cap_txt + flag_txt, "flags": flags}
    events = [str(x or "") for x in sig["vibha_event"].to_numpy()[: i + 1]]
    last = next((j for j in range(i - 1, -1, -1) if events[j]), None)
    since = ""
    if last is not None:
        names = [
            _SINCE.get(e, "the trim" if e.startswith("trim") else "the distribution cap")
            for e in events[last].split(",")
        ]
        since = f" since {' and '.join(names)} on {_date(sig, last)}"
    if v > 0:
        return out | {
            "rule": f"Holding {_pct(v)}",
            "detail": f"No new signal today; the position is unchanged{since}. It exits fully on a close below "
            f"the {c.exit_ma}-day EMA." + cap_txt + flag_txt,
            "flags": flags,
        }
    return out | {
        "rule": "Out of the market",
        "detail": f"No entry signal{since}: it waits for a follow-through day, {c.hhhl_days} days of higher highs "
        f"and lows, or a close back above the {c.reclaim_ma}-day average." + flag_txt,
        "flags": flags,
    }


def _blend(sig: pd.DataFrame, c: StrategyConfig, i: int, sleeves: list[dict]) -> tuple[list[str], float]:
    """The steps from sleeve values to the final net exposure; returns them and the net they produce."""
    r = sig.iloc[i]
    steps = []
    raw = sum((s["value"] or 0.0) * s["weight"] for s in sleeves)
    parts = " + ".join(f"{s['weight']:.2f} × {s['name'].lower()} {(s['value'] or 0.0):+.2f}" for s in sleeves)
    steps.append(f"Weighted sleeves: {parts} = {raw:+.2f}")
    net = max(-1.0, min(1.0, raw))
    if net != raw:
        steps.append(f"Clipped to {net:+.2f} (net exposure stays within ±1)")
    rv = _f(r["rv20"])
    if rv is not None and rv > c.vol_cap_threshold and net > c.vol_cap_long:
        net = c.vol_cap_long
        steps.append(
            f"QQQ 20-day volatility is {_pct(rv)} (above {_pct(c.vol_cap_threshold)}): longs capped at {net:+.2f}"
        )
    if c.vol_target > 0 and net > 0:
        scale = _f(r["vol_scale"]) or 1.0
        if scale < 1:
            net *= scale
            vol = _f(r["rv20"]) if c.vol_target_len == 20 else None
            vol_txt = f"QQQ volatility {_pct(vol)} is above" if vol is not None else "QQQ volatility is above"
            steps.append(f"Volatility target: {vol_txt} the {_pct(c.vol_target)} target, so × {scale:.2f} = {net:+.2f}")
        else:
            steps.append(f"Volatility target: QQQ volatility is under {_pct(c.vol_target)}, no scaling")
    long_w, short_w = max(net, 0) * c.max_long_alloc, max(-net, 0) * c.max_short_alloc
    if net > 0:
        steps.append(f"Net {net:+.2f} → {_pct(long_w)} of strategy capital in {c.long_symbol}")
    elif net < 0:
        steps.append(
            f"Net {net:+.2f} → {_pct(short_w)} in {c.short_symbol} "
            f"(shorts use at most {_pct(c.max_short_alloc)} of capital)"
        )
    else:
        steps.append("Net 0 → all cash")
    return steps, net


def explain(sig: pd.DataFrame, c: StrategyConfig, i: int = -1) -> dict:
    """Why day i of compute_signals' output has the exposure it has."""
    i = i % len(sig)
    sleeves = [_trend(sig, c, i), _mr(sig, c, i), _vibha(sig, c, i)]
    steps, net = _blend(sig, c, i, sleeves)
    r = sig.iloc[i]
    return {
        "version": VERSION,
        "date": str(pd.Timestamp(sig.index[i]).date()),
        "sleeves": sleeves,
        "blend": steps,
        "net": net,
        "inputs": {
            "close": _f(r["close"]),
            f"sma{c.trend_fast}": _f(r["sma_fast"]),
            f"sma{c.trend_slow}": _f(r["sma_slow"]),
            f"rsi{c.mr_rsi_len}": _f(r["rsi_mr"]),
            f"ema{c.exit_ma}": _f(r["ema_exit"]),
            f"sma{c.reclaim_ma}": _f(r["sma_reclaim"]),
            "rv20": _f(r["rv20"]),
        },
    }
