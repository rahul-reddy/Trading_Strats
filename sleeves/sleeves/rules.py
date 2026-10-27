"""The three sleeves' rules as pure functions of prices (and, for quality, a fundamentals screen).

Each returns target weights {symbol: fraction of the bucket} that sum to 1 (the cash ETF fills what the
rules leave empty) and one plain-language reason per symbol it considered. The backtest and the live run
call the same functions.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .config import SleeveConfig


@dataclass
class Decision:
    weights: dict[str, float]
    reasons: list[dict]  # {"symbol", "weight", "text"}, the chosen ones first
    summary: str


def _pct(x: float) -> str:
    return f"{x:+.1%}"


def market_filter(closes: pd.Series, months: int) -> tuple[bool, str]:
    """True while the latest close is above the average of the last `months` month-end closes (the latest
    close counts as this month's). Needs `months` month-ends of history; fewer is an error, not a guess."""
    s = closes.dropna()
    month_end = s.groupby(pd.DatetimeIndex(s.index).to_period("M")).last()
    if len(month_end) < months:
        raise ValueError(f"market filter needs {months} month-ends of {s.name}, have {len(month_end)}")
    avg = float(month_end.iloc[-months:].mean())
    last = float(s.iloc[-1])
    on = last > avg
    text = f"{s.name} {last:.2f} is {'above' if on else 'below'} its {months}-month average {avg:.2f}: " + (
        "stocks on" if on else "all in T-bills"
    )
    return on, text


def rotation(closes: pd.DataFrame, cfg: SleeveConfig) -> Decision:
    """Score = mean of the 1/3/6/12-month returns. Eligible: above its 210-day average with a positive score.
    Hold the best `top_n` eligible at 1/top_n each; empty slots go to the cash ETF."""
    need = max(max(cfg.score_lookbacks), cfg.trend_days) + 1
    uni = cfg.symbols()
    missing = [s for s in uni if s not in closes or closes[s].dropna().shape[0] < need]
    if missing:
        raise ValueError(f"rotation needs {need} days of history for {missing}")
    px = closes[uni]
    last = px.iloc[-1]
    score = pd.concat([last / px.iloc[-1 - n] - 1 for n in cfg.score_lookbacks], axis=1).mean(axis=1)
    sma = px.iloc[-cfg.trend_days :].mean()
    eligible = (last > sma) & (score > 0)
    ranked = score.sort_values(ascending=False)
    chosen = [s for s in ranked.index if eligible[s]][: cfg.top_n]
    w = {s: 1 / cfg.top_n for s in chosen}
    empty = cfg.top_n - len(chosen)
    if empty:
        w[cfg.cash_symbol] = w.get(cfg.cash_symbol, 0.0) + empty / cfg.top_n
    reasons = []
    for rank, s in enumerate(ranked.index, 1):
        trend = f"{_pct(last[s] / sma[s] - 1)} vs its {cfg.trend_days}-day average"
        if s in chosen:
            why = f"rank {rank}, score {_pct(score[s])}, {trend}: held at {1 / cfg.top_n:.0%}"
        elif not eligible[s]:
            failed = "below trend" if last[s] <= sma[s] else "negative score"
            why = f"rank {rank}, score {_pct(score[s])}, {trend}: not eligible ({failed})"
        else:
            why = f"rank {rank}, score {_pct(score[s])}, {trend}: eligible but not in the top {cfg.top_n}"
        reasons.append({"symbol": s, "weight": w.get(s, 0.0), "text": why})
    if empty:
        reasons.insert(
            0, {"symbol": cfg.cash_symbol, "weight": w[cfg.cash_symbol], "text": f"{empty} empty slot(s): T-bills"}
        )
    reasons.sort(key=lambda r: -r["weight"])
    summary = ", ".join(chosen) if chosen else "nothing eligible"
    return Decision(
        w, reasons, f"Top {cfg.top_n} eligible: {summary}" + (f"; {empty} slot(s) in T-bills" if empty else "")
    )


def sectors(closes: pd.DataFrame, cfg: SleeveConfig) -> Decision:
    """While the market filter is on, hold the `sector_top_n` sector ETFs with the best 12-month return
    skipping the latest month, equally; otherwise everything in the cash ETF. A sector ETF without 12
    months of history is left out (XLC and XLRE are younger than the others)."""
    on, ftext = market_filter(closes[cfg.filter_symbol], cfg.filter_months)
    need = cfg.mom_lookback + 1
    uni = [s for s in cfg.symbols() if s in closes and closes[s].dropna().shape[0] >= need]
    if len(uni) < cfg.sector_top_n:
        raise ValueError(f"sector momentum needs {need} days of history for at least {cfg.sector_top_n} sectors")
    px = closes[uni]
    mom = px.iloc[-1 - cfg.mom_skip] / px.iloc[-1 - cfg.mom_lookback] - 1
    ranked = mom.sort_values(ascending=False)
    chosen = list(ranked.index[: cfg.sector_top_n]) if on else []
    w = {s: 1 / cfg.sector_top_n for s in chosen} if on else {cfg.cash_symbol: 1.0}
    reasons = [
        {
            "symbol": s,
            "weight": w.get(s, 0.0),
            "text": f"rank {i}, 12-1 month return {_pct(mom[s])}"
            + (f": held at {1 / cfg.sector_top_n:.0%}" if s in chosen else ""),
        }
        for i, s in enumerate(ranked.index, 1)
    ]
    if not on:
        reasons.insert(0, {"symbol": cfg.cash_symbol, "weight": 1.0, "text": ftext})
    reasons.sort(key=lambda r: -r["weight"])
    summary = (f"Top {cfg.sector_top_n} sectors: " + ", ".join(chosen)) if on else "Market filter off: T-bills"
    return Decision(w, reasons, f"{summary}. {ftext}")


SCREEN_COLUMNS = ["Ticker", "Sector", "P/B", "ROE", "Operating Margin"]


def quality(screen: pd.DataFrame, closes: pd.DataFrame, cfg: SleeveConfig) -> Decision:
    """While the market filter is on: among S&P 500 stocks with positive book value and operating margin,
    keep those in the cheapest `q_cheap_fraction` by price-to-book AND the top `q_profit_fraction` by return
    on equity, rank them by the sum of the two ranks, and hold the best `q_max_names` (at most
    `q_max_per_sector` per sector) equally. Otherwise everything in the cash ETF."""
    missing = [c for c in SCREEN_COLUMNS if c not in screen]
    if missing:
        raise ValueError(f"quality screen is missing columns {missing}")
    if len(screen) < 400:  # the S&P 500 has ~500 names; far fewer means a broken export, not a small market
        raise ValueError(f"quality screen returned only {len(screen)} stocks")
    on, ftext = market_filter(closes[cfg.filter_symbol], cfg.filter_months)
    if not on:
        return Decision(
            {cfg.cash_symbol: 1.0},
            [{"symbol": cfg.cash_symbol, "weight": 1.0, "text": ftext}],
            f"Market filter off. {ftext}",
        )
    q = screen.dropna(subset=["P/B", "ROE", "Operating Margin"])
    q = q[(q["P/B"] > 0) & (q["Operating Margin"] > 0)].copy()
    cheap_cut = q["P/B"].quantile(cfg.q_cheap_fraction)
    profit_cut = q["ROE"].quantile(1 - cfg.q_profit_fraction)
    q = q[(q["P/B"] <= cheap_cut) & (q["ROE"] >= profit_cut)]
    q = q.assign(rank=q["P/B"].rank() + q["ROE"].rank(ascending=False)).sort_values(["rank", "Ticker"])
    picked = q.groupby("Sector", sort=False).head(cfg.q_max_per_sector).head(cfg.q_max_names)
    if picked.empty:
        raise ValueError("quality screen kept no stocks")
    n = len(picked)
    w = {t: 1 / n for t in picked["Ticker"]}
    reasons = [
        {
            "symbol": r["Ticker"],
            "weight": 1 / n,
            "text": f"{r['Sector']}: P/B {r['P/B']:.2f} (cheapest {cfg.q_cheap_fraction:.0%} is at or under "
            f"{cheap_cut:.2f}), ROE {r['ROE']:.0%} (top {cfg.q_profit_fraction:.0%} starts at {profit_cut:.0%})",
        }
        for _, r in picked.iterrows()
    ]
    return Decision(w, reasons, f"{n} cheap and profitable stocks, at most {cfg.q_max_per_sector} per sector. {ftext}")
