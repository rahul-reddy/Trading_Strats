"""Intraday quotes for the near-close run: from the broker, or from the Finviz Elite export API.

Finviz Elite quotes are real time, which matters for an IBKR paper account without a market-data
subscription (its quotes are 15 minutes delayed). The token comes from an environment variable,
never from the config file. If Finviz fails, the broker's quote is used and the run records that.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import urllib.request

log = logging.getLogger("tqqq_blend")
FINVIZ_EXPORT = "https://elite.finviz.com/export.ashx?v=152&c=1,65&t={tickers}&auth={token}"  # c=1,65: ticker, price


def finviz_prices(symbols, token: str, timeout: float = 10) -> dict[str, float]:
    url = FINVIZ_EXPORT.format(tickers=",".join(symbols), token=token)
    req = urllib.request.Request(url, headers={"User-Agent": "tqqq-blend"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8-sig")
    prices = {row["Ticker"]: float(row["Price"]) for row in csv.DictReader(io.StringIO(body)) if row.get("Price")}
    missing = [s for s in symbols if not prices.get(s, 0) > 0]
    if missing:  # an expired token returns an HTML login page, which parses to no rows
        raise RuntimeError(f"Finviz returned no price for {missing}")
    return {s: prices[s] for s in symbols}


def get_prices(cfg, broker, symbols) -> tuple[dict[str, float], str]:
    """Prices for `symbols` and the source they came from ("finviz" or the broker's name)."""
    b = cfg.broker
    if b.quote_source == "finviz":
        token = os.environ.get(b.finviz_token_env, "")
        try:
            if not token:
                raise RuntimeError(f"environment variable {b.finviz_token_env} is not set")
            return finviz_prices(symbols, token), "finviz"
        except Exception as e:  # noqa: BLE001 - fall back to the broker rather than skip the day
            log.warning("Finviz quotes failed (%s); using %s quotes", e, broker.name)
    elif b.quote_source != "broker":
        raise ValueError(f"quote_source must be 'broker' or 'finviz', not '{b.quote_source}'")
    return {s: broker.last_price(s) for s in symbols}, broker.name
