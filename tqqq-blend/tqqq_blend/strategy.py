"""The blended TQQQ/SQQQ strategy.

Three sleeves are computed on daily QQQ bars and blended into one net exposure in [-1, 1]:

  trend  (Mallik)  - regime from 20/250-day SMAs, size scaled by extension from the 250-day,
                     full size in Bollinger momentum phases, SQQQ in confirmed downtrends.
  mr     (Mallik)  - short-term mean reversion with RSI(2): buy dips in uptrends, fade rallies
                     in downtrends, small bounce trade when deeply oversold.
  vibha  (Jha)     - long-only market timing: follow-through day -> 100%, three days of higher
                     highs/lows -> 25-75% pilot, retake of 50-day -> 50%; trims as sell flags
                     stack up (distribution days, 3 down days, 10-day lost on volume, new high on
                     weak volume) and exits fully below the 21-day EMA.

Volatility target: the blended net long is scaled by min(1, vol_target / QQQ 20-day realized vol).

Positive net -> TQQQ weight, negative net -> SQQQ weight. Signals use the close of day t and are
meant to be executed in the last minutes of day t (both traders act near the close).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .indicators import bollinger, ema, realized_vol, rsi, sma


def add_indicators(df: pd.DataFrame, c: StrategyConfig) -> pd.DataFrame:
    out = df.copy()
    close = out["close"]
    out["sma_fast"] = sma(close, c.trend_fast)
    out["sma_slow"] = sma(close, c.trend_slow)
    out["ext"] = close / out["sma_slow"] - 1
    _, _, out["bb_upper"] = bollinger(close, c.bb_len, c.bb_k)
    out["rsi_mr"] = rsi(close, c.mr_rsi_len)
    out["sma_short"] = sma(close, c.short_ma)
    out["ema_exit"] = ema(close, c.exit_ma)
    out["sma_reclaim"] = sma(close, c.reclaim_ma)
    out["hi_52w"] = close.rolling(252, min_periods=252).max()
    out["vol_avg"] = sma(out["volume"], c.vol_avg_len)
    out["rv20"] = realized_vol(close, 20)
    return out


def trend_sleeve(d: pd.DataFrame, c: StrategyConfig) -> np.ndarray:
    close, slow, fast = d["close"].to_numpy(), d["sma_slow"].to_numpy(), d["sma_fast"].to_numpy()
    ext, bbu = d["ext"].to_numpy(), d["bb_upper"].to_numpy()
    fast_falling = (d["sma_fast"] < d["sma_fast"].shift(c.short_slope_lookback)).to_numpy()
    out = np.zeros(len(d))
    regime = 0  # +1 bull, -1 bear (hysteresis around the 250-day)
    for i in range(len(d)):
        if np.isnan(slow[i]) or np.isnan(fast[i]):
            continue
        if close[i] > slow[i] * (1 + c.trend_hysteresis):
            regime = 1
        elif close[i] < slow[i] * (1 - c.trend_hysteresis):
            regime = -1
        if regime == 1:
            size = c.ext_size2 if ext[i] > c.ext_tier2 else c.ext_size1 if ext[i] > c.ext_tier1 else 1.0
            if close[i] < fast[i]:
                size = min(size, c.below_fast_size)
            if not np.isnan(bbu[i]) and close[i] > bbu[i]:
                size = 1.0
            out[i] = size
        elif regime == -1 and c.short_enabled and close[i] < fast[i] and fast_falling[i]:
            out[i] = -1.0
    return out


def mr_sleeve(d: pd.DataFrame, c: StrategyConfig) -> np.ndarray:
    close, slow = d["close"].to_numpy(), d["sma_slow"].to_numpy()
    ext, r = d["ext"].to_numpy(), d["rsi_mr"].to_numpy()
    out = np.zeros(len(d))
    pos, held = 0.0, 0
    for i in range(len(d)):
        if np.isnan(slow[i]) or np.isnan(r[i]):
            continue
        if pos != 0:
            held += 1
            if pos > 0 and (r[i] > c.mr_long_exit or held >= c.mr_max_hold):
                pos = 0.0
            elif pos < 0 and (r[i] < c.mr_short_exit or held >= c.mr_max_hold or close[i] > slow[i]):
                pos = 0.0
        elif close[i] > slow[i] and r[i] < c.mr_long_entry:
            pos, held = 1.0, 0
        elif close[i] < slow[i] and ext[i] < c.mr_deep_ext and r[i] < c.mr_deep_entry:
            pos, held = c.mr_deep_size, 0
        elif close[i] < slow[i] and r[i] > c.mr_short_entry and c.mr_short_enabled:
            pos, held = -1.0, 0
        out[i] = pos
    return out


def vibha_sleeve(d: pd.DataFrame, c: StrategyConfig) -> pd.DataFrame:
    """Path-dependent state machine. Returns exposure plus diagnostics per day."""
    o, h, lo, cl, v = (d[k].to_numpy() for k in ("open", "high", "low", "close", "volume"))
    sma_s, ema_x, sma_r = d["sma_short"].to_numpy(), d["ema_exit"].to_numpy(), d["sma_reclaim"].to_numpy()
    hi52, vavg, slow = d["hi_52w"].to_numpy(), d["vol_avg"].to_numpy(), d["sma_slow"].to_numpy()
    n = len(d)
    exp_arr, nd_arr, flags_arr = np.zeros(n), np.zeros(n, int), np.zeros(n, int)
    event = np.array([""] * n, dtype=object)

    exp = 0.0
    dist: list[tuple[int, float]] = []
    attempt_low, day1 = np.inf, None
    prev_flags = 0
    warm = max(c.exit_ma, c.reclaim_ma, c.vol_avg_len, c.hhhl_days + 1)
    for i in range(1, n):
        chg = cl[i] / cl[i - 1] - 1
        higher_vol = v[i] > v[i - 1]
        if chg <= -c.dist_min_drop and higher_vol:
            dist.append((i, cl[i]))
        dist = [(j, dc) for j, dc in dist if i - j < c.dist_window and cl[i] < dc * (1 + c.dist_expiry_rally)]
        nd = len(dist)
        nd_arr[i] = nd
        if i < warm or np.isnan(ema_x[i]):
            continue

        # Rally-attempt bookkeeping (only while light): undercutting the low resets the count.
        if exp <= 0.5:
            if lo[i] < attempt_low:
                attempt_low, day1 = lo[i], None
            elif day1 is None and chg > 0:
                day1 = i

        ev = []
        # ---------------- sells first ----------------
        f_new_high_weak = not np.isnan(hi52[i]) and cl[i] >= hi52[i] and v[i] < vavg[i]
        three_down = cl[i] < cl[i - 1] < cl[i - 2] < cl[i - 3]
        f_three_down_vol = (
            three_down and v[i] > v[i - 1] > v[i - 2] and h[i] < h[i - 1] < h[i - 2] and lo[i] < lo[i - 1] < lo[i - 2]
        )
        f_lost_10 = cl[i] < sma_s[i] and higher_vol
        flags = int(f_new_high_weak) + int(nd >= c.dist_warn) + int(three_down) + int(f_lost_10) + int(f_three_down_vol)

        exited = False
        if exp > 0:
            if cl[i] < ema_x[i]:
                exp, exited = 0.0, True
                ev.append("exit<21ema")
                attempt_low, day1 = lo[i], None
            else:
                if flags >= c.flags_to_trim and flags > prev_flags:
                    exp *= 1 - c.trim_fraction
                    ev.append(f"trim({flags}flags)")
                if nd >= c.dist_heavy and exp > c.dist_heavy_cap:
                    exp = c.dist_heavy_cap
                    ev.append(f"distcap({nd})")
        prev_flags = flags

        # ---------------- buys ----------------
        if not exited:
            is_ftd = (
                day1 is not None
                and i - day1 + 1 >= c.ftd_min_day
                and chg >= c.ftd_min_gain
                and higher_vol
                and exp <= 0.5
            )
            k = c.hhhl_days
            hhhl = all(h[i - j] > h[i - j - 1] and lo[i - j] > lo[i - j - 1] for j in range(k))
            if is_ftd:
                exp = 1.0
                dist = []
                day1 = None
                attempt_low = np.inf
                ev.append("FTD")
            elif hhhl and nd < c.dist_warn and cl[i] > o[i]:
                size = c.hhhl_base_size + 0.25 * (cl[i] > ema_x[i]) + 0.25 * (cl[i] > sma_r[i])
                size = min(size, c.hhhl_max_size)
                if size > exp:
                    exp = size
                    ev.append("HHHL")
            elif cl[i] > sma_r[i] and cl[i - 1] <= sma_r[i - 1] and exp < c.reclaim_size and cl[i] > ema_x[i]:
                exp = c.reclaim_size
                ev.append("reclaim50")
        # Blend rule: Mallik's long-term regime governs Vibha's size. Under the 250-day, follow-through
        # days fail often (2000-02, 2008), so they only buy a pilot position.
        if cl[i] < slow[i] and exp > c.bear_regime_cap:
            exp = c.bear_regime_cap
        if exp > 0.5:
            attempt_low, day1 = np.inf, None

        exp_arr[i], flags_arr[i], event[i] = exp, flags, ",".join(ev)
    return pd.DataFrame(
        {"vibha": exp_arr, "dist_days": nd_arr, "sell_flags": flags_arr, "vibha_event": event}, index=d.index
    )


def compute_signals(df: pd.DataFrame, c: StrategyConfig) -> pd.DataFrame:
    """df: daily OHLCV of the signal symbol (lowercase columns). Returns indicators + targets."""
    d = add_indicators(df, c)
    d["trend"] = trend_sleeve(d, c)
    d["mr"] = mr_sleeve(d, c)
    d = d.join(vibha_sleeve(d, c))
    net = c.weight_trend * d["trend"] + c.weight_mr * d["mr"] + c.weight_vibha * d["vibha"]
    net = net.clip(-1, 1)
    hot = d["rv20"] > c.vol_cap_threshold
    net = net.where(~(hot & (net > c.vol_cap_long)), c.vol_cap_long)
    if c.vol_target > 0:
        rv = d["rv20"] if c.vol_target_len == 20 else realized_vol(d["close"], c.vol_target_len)
        d["vol_scale"] = (c.vol_target / rv).clip(upper=1.0).fillna(1.0)
        net = net.where(net <= 0, net * d["vol_scale"])
    else:
        d["vol_scale"] = 1.0
    d["net"] = net
    d["w_long"] = net.clip(lower=0) * c.max_long_alloc
    d["w_short"] = (-net).clip(lower=0) * c.max_short_alloc
    d["ready"] = d["sma_slow"].notna()
    return d
