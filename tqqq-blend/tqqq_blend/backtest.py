"""Daily backtest with synthetic 3x long/short ETFs built from QQQ (real TQQQ/SQQQ start in 2010).

Synthetic TQQQ = 3 * r_qqq - (expense + 2 * (risk-free + spread)) / 252   (cost of 2x borrowed notional)
Synthetic SQQQ = -3 * r_qqq - expense / 252                      (conservative: no collateral yield)
Decision at close t, position earns day t+1 return. Trades cost `cost_bps` of traded notional.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .data import daily_bars, risk_free_daily
from .strategy import compute_signals

EXPENSE = 0.0095
SWAP_SPREAD = 0.005  # financing spread over T-bills; makes synthetic TQQQ CAGR match the real ETF since 2010


def synthetic_leveraged(qqq: pd.DataFrame, rf_d: pd.Series, spread: float = SWAP_SPREAD) -> pd.DataFrame:
    r = qqq["close"].pct_change().fillna(0)
    return pd.DataFrame({
        "r_qqq": r,
        "r_long": 3 * r - (EXPENSE / 252 + 2 * (rf_d + spread / 252)),
        "r_short": -3 * r - EXPENSE / 252,
        "rf": rf_d,
    }, index=qqq.index)


def run(sig: pd.DataFrame, rets: pd.DataFrame, band: float, cost_bps: float = 5.0) -> pd.DataFrame:
    rl, rs, rf = (rets[k].to_numpy() for k in ("r_long", "r_short", "rf"))
    wl_t, ws_t = sig["w_long"].to_numpy(), sig["w_short"].to_numpy()
    ready = sig["ready"].to_numpy()
    n = len(sig)
    eq = np.ones(n)
    wl_hist, ws_hist, traded = np.zeros(n), np.zeros(n), np.zeros(n)
    pl = ps = 0.0
    cash = 1.0
    for i in range(1, n):
        pl *= 1 + rl[i]
        ps *= 1 + rs[i]
        cash *= 1 + rf[i]
        e = pl + ps + cash
        if not ready[i]:
            eq[i] = e
            continue
        wl, ws = pl / e, ps / e
        tl, ts = wl_t[i], ws_t[i]
        need = (abs(tl - wl) > band or abs(ts - ws) > band
                or (tl == 0 and wl > 0) or (ts == 0 and ws > 0)
                or (tl > 0 and wl == 0) or (ts > 0 and ws == 0))
        if need:
            turnover = abs(tl - wl) + abs(ts - ws)
            e -= turnover * e * cost_bps / 1e4
            pl, ps = tl * e, ts * e
            cash = e - pl - ps
            traded[i] = turnover
        eq[i] = e
        wl_hist[i], ws_hist[i] = pl / e, ps / e
    return pd.DataFrame({"equity": eq, "w_long": wl_hist, "w_short": ws_hist, "turnover": traded},
                        index=sig.index)


def stats(equity: pd.Series, turnover: pd.Series | None = None, w=None, rf: pd.Series | None = None) -> dict:
    """Sharpe is on returns in excess of T-bills when rf is given."""
    r = equity.pct_change().dropna()
    ex = r - rf.reindex(r.index).fillna(0) if rf is not None else r
    yrs = len(r) / 252
    cagr = equity.iloc[-1] ** (1 / yrs) - 1
    dd = equity / equity.cummax() - 1
    out = {
        "CAGR": cagr,
        "MaxDD": dd.min(),
        "Vol": r.std() * np.sqrt(252),
        "Sharpe": ex.mean() / ex.std() * np.sqrt(252) if ex.std() > 0 else np.nan,
        "Calmar": cagr / abs(dd.min()) if dd.min() < 0 else np.nan,
        "Final x": equity.iloc[-1],
    }
    if turnover is not None:
        out["Trades/yr"] = (turnover > 0).sum() / yrs
    if w is not None:
        out["Time long"] = (w["w_long"] > 0).mean()
        out["Time short"] = (w["w_short"] > 0).mean()
    return out


def annual_returns(equity: pd.Series) -> pd.Series:
    return equity.resample("YE").last().pct_change().fillna(equity.resample("YE").last().iloc[0] - 1)


def backtest(cfg: StrategyConfig, start: str = "1999-03-10", cost_bps: float = 5.0, refresh=False):
    qqq = daily_bars(cfg.signal_symbol, refresh=refresh)
    rets = synthetic_leveraged(qqq, risk_free_daily(qqq.index, refresh=refresh))
    variants = {
        "Blend": cfg,
        "Blend v1 (0.4/0.2/0.4, no vol target)": replace(cfg, weight_trend=0.4, weight_mr=0.2, weight_vibha=0.4,
                                                         vol_target=0),
        "Trend only (Mallik TF)": replace(cfg, weight_trend=1, weight_mr=0, weight_vibha=0),
        "MR only (Mallik MR)": replace(cfg, weight_trend=0, weight_mr=1, weight_vibha=0),
        "Vibha only": replace(cfg, weight_trend=0, weight_mr=0, weight_vibha=1),
    }
    results, curves, sig_main = {}, {}, None
    for name, c in variants.items():
        sig = compute_signals(qqq, c)
        res = run(sig, rets, c.rebalance_band, cost_bps)
        res = res[res.index >= pd.Timestamp(start)]
        res["equity"] /= res["equity"].iloc[0]
        results[name] = stats(res["equity"], res["turnover"], res, rets["rf"])
        curves[name] = res["equity"]
        if name == "Blend":
            sig_main, res_main = sig, res
    idx = curves["Blend"].index
    # Simplest rule a timing strategy has to beat: 100% TQQQ while QQQ is above its 200-day, else T-bills.
    above = (qqq["close"] > qqq["close"].rolling(200).mean()).astype(float)
    sma200 = run(pd.DataFrame({"w_long": above, "w_short": 0.0, "ready": True}), rets, 0.0, cost_bps)
    sma200 = sma200[sma200.index >= pd.Timestamp(start)]
    results["TQQQ when QQQ > SMA200"] = stats(sma200["equity"] / sma200["equity"].iloc[0], sma200["turnover"],
                                              sma200, rets["rf"])
    curves["TQQQ when QQQ > SMA200"] = sma200["equity"] / sma200["equity"].iloc[0]
    for name, r in (("QQQ buy&hold", rets["r_qqq"]), ("Synthetic TQQQ buy&hold", rets["r_long"])):
        e = (1 + r.reindex(idx).fillna(0)).cumprod()
        e.iloc[0] = 1.0
        results[name] = stats(e, rf=rets["rf"])
        curves[name] = e
    return pd.DataFrame(results).T, pd.DataFrame(curves), sig_main, res_main


def validate_synthetic(refresh=False) -> dict:
    """How well the synthetic TQQQ tracks the real one since its 2010 launch."""
    qqq = daily_bars("QQQ", refresh=refresh)
    real = daily_bars("TQQQ", refresh=refresh)["close"].pct_change().dropna()
    syn = synthetic_leveraged(qqq, risk_free_daily(qqq.index))["r_long"].reindex(real.index)
    yrs = len(real) / 252
    return {
        "daily_corr": real.corr(syn),
        "real_cagr": (1 + real).prod() ** (1 / yrs) - 1,
        "synthetic_cagr": (1 + syn).prod() ** (1 / yrs) - 1,
    }
