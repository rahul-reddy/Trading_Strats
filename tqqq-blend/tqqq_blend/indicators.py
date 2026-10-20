"""Plain pandas/numpy indicators. Everything is computed on the daily signal series (QQQ)."""

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(s: pd.Series, n: int) -> pd.Series:
    """Wilder RSI. Returns 100 when there were no down moves in the window."""
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    out = 100 - 100 / (1 + up / dn.where(dn > 0))
    return out.where(~((dn == 0) & up.notna()), 100.0)


def bollinger(s: pd.Series, n: int = 20, k: float = 2.0):
    mid = sma(s, n)
    sd = s.rolling(n, min_periods=n).std(ddof=0)
    return mid - k * sd, mid, mid + k * sd


def realized_vol(s: pd.Series, n: int = 20) -> pd.Series:
    return s.pct_change().rolling(n, min_periods=n).std() * np.sqrt(252)
