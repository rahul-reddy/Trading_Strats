"""Market data. Daily closes come from yfinance (free, end of day); today's live prices and the S&P 500
fundamentals screen come from the Finviz Elite export API. A missing or stale input is an error that stops
the run, never a guess. The Finviz token comes from an environment variable and is never logged.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import urllib.request
import warnings
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

from .schedule import trading_days

log = logging.getLogger("sleeves")
FINVIZ = "https://elite.finviz.com/export.ashx?{query}&auth={token}"
SCREEN_VIEWS = (111, 121, 161)  # overview (sector), valuation (P/B), financial (ROE, operating margin)


def _download(symbols: list[str], start: str) -> pd.DataFrame:
    with warnings.catch_warnings():  # yfinance's own pandas deprecation warnings, one per symbol
        warnings.simplefilter("ignore")
        raw = yf.download(symbols, start=start, auto_adjust=True, progress=False, threads=False)
    if raw is None or raw.empty:
        raise RuntimeError(f"no price history returned for {symbols}")
    closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]].set_axis(symbols, axis=1)
    closes.index = pd.DatetimeIndex(pd.to_datetime(closes.index).tz_localize(None).normalize(), name="date")
    return closes.astype(float)


def history(symbols: list[str], today: date, years: int = 2) -> pd.DataFrame:
    """Dividend-adjusted daily closes through the previous trading day (a bar for today, if yfinance has one
    yet, is dropped: the live price replaces it). Raises if any symbol lacks the previous trading day."""
    start = (pd.Timestamp(today) - pd.DateOffset(years=years)).strftime("%Y-%m-%d")  # yfinance wants a bare date
    closes = _download(sorted(set(symbols)), start)
    closes = closes[closes.index < pd.Timestamp(today)]
    days = trading_days((pd.Timestamp(today) - pd.Timedelta(days=10)).date(), today)
    prev = [d for d in days if d < today][-1]
    stale = [s for s in symbols if closes[s].dropna().empty or closes[s].dropna().index[-1].date() < prev]
    if stale:
        raise RuntimeError(f"stale history: {stale} have no close for {prev}")
    return closes


def with_live(closes: pd.DataFrame, prices: dict[str, float], today: date) -> pd.DataFrame:
    """The history plus a row for today at the live prices (the near-close stand-in for today's close)."""
    row = pd.DataFrame([prices], index=pd.DatetimeIndex([pd.Timestamp(today)], name="date"))
    return pd.concat([closes, row.reindex(columns=closes.columns)])


def _finviz(query: str, token: str, timeout: float = 30) -> list[dict]:
    req = urllib.request.Request(FINVIZ.format(query=query, token=token), headers={"User-Agent": "sleeves"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(body)))


def finviz_token(env: str) -> str:
    token = os.environ.get(env, "")
    if not token:
        raise RuntimeError(f"environment variable {env} is not set")
    return token


def finviz_prices(symbols: list[str], token: str) -> dict[str, float]:
    rows = _finviz("v=152&c=1,65&t=" + ",".join(symbols), token)  # c=1,65: ticker, price
    prices = {r["Ticker"]: float(r["Price"]) for r in rows if r.get("Price")}
    missing = [s for s in symbols if not prices.get(s, 0) > 0]
    if missing:  # an expired token returns an HTML login page, which parses to no rows
        raise RuntimeError(f"Finviz returned no price for {missing}")
    return {s: prices[s] for s in symbols}


def get_prices(cfg, broker, symbols: list[str]) -> tuple[dict[str, float], str]:
    """Live prices and their source. Finviz first when configured; if it fails, the broker's quotes, and the
    run records which source it used."""
    b = cfg.broker
    if b.quote_source == "finviz":
        try:
            return finviz_prices(symbols, finviz_token(b.finviz_token_env)), "finviz"
        except Exception as e:  # noqa: BLE001 - fall back to the broker rather than skip the day
            log.warning("Finviz quotes failed (%s); using %s quotes", e, broker.name)
    elif b.quote_source != "broker":
        raise ValueError(f"quote_source must be 'broker' or 'finviz', not '{b.quote_source}'")
    return {s: broker.last_price(s) for s in symbols}, broker.name


def _number(x) -> float | None:
    s = str(x).strip().replace(",", "")
    pct = s.endswith("%")
    try:
        v = float(s.rstrip("%"))
    except ValueError:
        return None
    return v / 100 if pct else v


def sp500_screen(token: str) -> pd.DataFrame:
    """Ticker, Sector, P/B, ROE and Operating Margin for every S&P 500 stock, from Finviz. Percentages come
    back as fractions (0.25 = 25%). No fallback: the quality sleeve doesn't trade without it."""
    frames = []
    for v in SCREEN_VIEWS:
        rows = _finviz(f"v={v}&f=idx_sp500", token)
        if not rows:
            raise RuntimeError(f"Finviz screen view {v} returned no rows (expired token?)")
        frames.append(pd.DataFrame(rows))
    df = frames[0][["Ticker", "Sector"]]
    for f in frames[1:]:
        df = df.merge(f[[c for c in f.columns if c not in df.columns or c == "Ticker"]], on="Ticker")
    df = df.rename(columns={"Return on Equity": "ROE"})
    for c in ("P/B", "ROE", "Operating Margin"):
        if c not in df:
            raise RuntimeError(f"Finviz screen has no '{c}' column")
        df[c] = df[c].map(_number)
    return df[["Ticker", "Sector", "P/B", "ROE", "Operating Margin"]]


def cached_history(symbols: list[str], start: str, cache: Path, refresh: bool = False) -> pd.DataFrame:
    """Full daily closes for the backtest, cached in state/cache."""
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / "closes.csv"
    if path.exists() and not refresh:
        closes = pd.read_csv(path, index_col=0, parse_dates=True)
        if set(symbols) <= set(closes.columns):
            return closes[symbols]
    closes = _download(sorted(set(symbols)), start)
    closes.to_csv(path)
    return closes[symbols]
