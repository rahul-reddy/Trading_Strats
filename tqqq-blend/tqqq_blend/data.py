"""Market data via yfinance (free, end-of-day). Cached to state/cache to avoid re-downloading."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import yfinance as yf

CACHE = Path("state/cache")


def daily_bars(symbol: str, start: str = "1990-01-01", refresh: bool = False) -> pd.DataFrame:
    """Dividend/split-adjusted daily OHLCV with lowercase columns and a tz-naive date index."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol.replace('^', '_')}.csv"
    if path.exists() and not refresh:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
    else:
        raw = yf.Ticker(symbol).history(start=start, auto_adjust=True)
        if raw.empty:
            raise RuntimeError(f"No data returned for {symbol}")
        df = raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        df.index = pd.DatetimeIndex(df.index.tz_localize(None).normalize(), name="date")
        df.to_csv(path)
    return df[df.index >= pd.Timestamp(start)]


def risk_free_daily(index: pd.Index, refresh: bool = False) -> pd.Series:
    """Daily T-bill return from ^IRX (13-week yield, percent)."""
    irx = daily_bars("^IRX", refresh=refresh)["close"]
    rate = irx.reindex(index).ffill().bfill().fillna(2.0) / 100
    return rate / 252


def live_signal_bars(symbol: str, live_price: float | None, volume_projection: float) -> pd.DataFrame:
    """Recent history with today's in-progress bar, its close replaced by the live price and its
    volume projected to a full day. Used by the near-close runner."""
    raw = yf.Ticker(symbol).history(period="2y", auto_adjust=True)
    # float: volume arrives as int64, and pandas 3 refuses to store the projected (fractional) volume in it
    df = raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].astype(float)
    df.index = pd.DatetimeIndex(df.index.tz_localize(None).normalize(), name="date")
    today = pd.Timestamp.now(tz="America/New_York").tz_localize(None).normalize()
    if df.index[-1] == today:
        if live_price:
            df.loc[today, "close"] = live_price
            df.loc[today, "high"] = max(df.loc[today, "high"], live_price)
            df.loc[today, "low"] = min(df.loc[today, "low"], live_price)
        df.loc[today, "volume"] = df.loc[today, "volume"] * volume_projection
    return df
