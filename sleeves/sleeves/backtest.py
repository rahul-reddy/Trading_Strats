"""Backtest of the rotation and sector-momentum sleeves with the live rules (rules.py) on daily ETF closes.

Each month-end close the rules pick target weights from the closes up to that day; the weights are held
from the next day. Trading costs are charged on turnover; the cash ETF earns the 13-week T-bill rate (^IRX),
because SGOV only exists from 2020. Quality-value can't be backtested this way: it needs point-in-time
fundamentals for the whole S&P 500, which no free source has (STRATEGY.md has the CRSP-based proxy).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from . import rules
from .config import SleeveConfig
from .data import cached_history


def stats(r: pd.Series) -> dict:
    r = r.dropna()
    eq = (1 + r).cumprod()
    yrs = len(r) / 252
    vol = r.std() * np.sqrt(252)
    return {
        "CAGR": eq.iloc[-1] ** (1 / yrs) - 1,
        "Vol": vol,
        "MaxDD": (eq / eq.cummax() - 1).min(),
        "Sharpe": r.mean() * 252 / vol if vol else np.nan,
        "Worst 12m": ((1 + r).rolling(252).apply(np.prod, raw=True) - 1).min(),
        "Years": yrs,
    }


def simulate(
    closes: pd.DataFrame, cash: pd.Series, cfg: SleeveConfig, start: str, cost: float
) -> tuple[pd.Series, pd.DataFrame]:
    """Daily returns of the sleeve, and the weights it held, from `start`."""
    rets = closes.pct_change(fill_method=None).fillna(0)
    rets[cfg.cash_symbol] = cash.reindex(closes.index).fillna(0)
    idx = pd.DatetimeIndex(closes.index)
    month_ends = idx.to_series().groupby(idx.to_period("M")).last()
    month_ends = month_ends[month_ends >= pd.Timestamp(start)]
    w = pd.DataFrame(np.nan, index=closes.index, columns=rets.columns)
    decide = rules.rotation if cfg.kind == "rotation" else rules.sectors
    for d in month_ends:
        hist = closes.loc[:d]
        try:
            weights = decide(hist, cfg).weights
        except ValueError:  # not enough history yet
            continue
        w.loc[d] = 0.0
        for s, x in weights.items():
            w.loc[d, s] = x
    w = w.ffill().fillna(0)
    held = w.shift(1).fillna(0)  # decided at the close, held from the next day
    turnover = w.diff().abs().sum(axis=1).shift(1).fillna(0)
    r = (held * rets).sum(axis=1) - turnover * cost
    first = w.index[int((w.sum(axis=1) > 0).to_numpy().argmax())]
    return r.loc[first:], w.loc[first:]


def run(kind: str, start: str, cost_bps: float, cache: Path, refresh: bool = False) -> dict[str, pd.DataFrame]:
    cfg = replace(SleeveConfig(), kind=kind)
    symbols = sorted(set(cfg.symbols()) | {cfg.filter_symbol, cfg.benchmark})
    closes = cached_history(symbols + ["^IRX"], "1998-01-01", cache, refresh)
    cash = (closes.pop("^IRX").ffill() / 100 / 252).fillna(0)
    closes = closes.loc[: closes.dropna(how="all").index[-1]]
    variants = {"as configured": cfg}
    if kind == "rotation":
        variants |= {f"top {n}": replace(cfg, top_n=n) for n in (3, 5)}
    else:
        variants |= {f"top {n}": replace(cfg, sector_top_n=n) for n in (2, 4)}
    out = {}
    curves = {}
    for name, v in variants.items():
        r, _ = simulate(closes, cash, v, start, cost_bps / 10_000)
        curves[name] = r
    first = curves["as configured"].index[0]
    bench = closes[cfg.benchmark].pct_change(fill_method=None).loc[first:]
    curves[f"{cfg.benchmark} buy and hold"] = bench
    main = curves["as configured"]
    mid = main.index[len(main) // 2]
    for label, sl in (("Full", slice(None)), ("First half", slice(None, mid)), ("Second half", slice(mid, None))):
        span = main.loc[sl].index
        title = f"{label} ({span[0].date()} to {span[-1].date()})"
        out[title] = pd.DataFrame({k: stats(c.loc[sl]) for k, c in curves.items()}).T
    return out
