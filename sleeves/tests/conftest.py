"""Shared fakes: synthetic closes, a Finviz-like S&P 500 screen, and one broker account shared by buckets."""

from itertools import count

import numpy as np
import pandas as pd
import pytest

from sleeves.brokers import Fill

ET = "America/New_York"


def closes(symbols, end="2026-10-29", days=400, drift=None, seed=3):
    """Business-day closes ending `end`. drift: {symbol: daily drift}; others drift +0.0002."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=end, periods=days)
    data = {}
    for s in symbols:
        mu = (drift or {}).get(s, 0.0002)
        data[s] = 100 * np.cumprod(1 + mu + rng.normal(0, 0.004, len(idx)))
    return pd.DataFrame(data, index=pd.DatetimeIndex(idx, name="date"))


def screen(n=500, seed=5):
    """An S&P-500-sized fundamentals table with known cheap-and-profitable names Q000..Q029."""
    rng = np.random.default_rng(seed)
    sectors = ["Technology", "Financial", "Healthcare", "Energy", "Industrials", "Utilities"]
    rows = []
    for i in range(n):
        good = i < 30
        rows.append(
            {
                "Ticker": f"Q{i:03d}",
                "Sector": sectors[i % len(sectors)],
                "P/B": 0.8 + i * 0.01 if good else 3 + rng.random() * 10,
                "ROE": 0.40 - i * 0.001 if good else rng.random() * 0.15,
                "Operating Margin": 0.2,
            }
        )
    return pd.DataFrame(rows)


class Account:
    def __init__(self):
        self.positions: dict[str, int] = {}
        self.fills: list[Fill] = []
        self.queued: list = []
        self.ids = count(1)
        self.prices: dict[str, float] = {}

    def execute(self, order, when):
        self.positions[order.symbol] = self.positions.get(order.symbol, 0) + order.signed_qty
        px = self.prices[order.symbol]
        self.fills.append(Fill(f"x{next(self.ids)}", order.ref, order.symbol, order.signed_qty, px, 1.0, when))

    def close(self, day):
        for o in self.queued:
            self.execute(o, f"{day}T16:00:00-04:00")
        self.queued = []


class FakeBroker:
    name = "ibkr"
    reports_fills = True

    def __init__(self, account, day):
        self.account, self.day = account, day

    def connect(self):
        pass

    def disconnect(self):
        pass

    def positions(self):
        return {k: v for k, v in self.account.positions.items() if v}

    def net_liquidation(self):
        return 1e9

    def last_price(self, symbol):
        return self.account.prices[symbol]

    def place(self, order):
        if order.order_type == "MOC":
            self.account.queued.append(order)
        else:
            self.account.execute(order, f"{self.day}T15:55:00-04:00")
        return "fake Submitted"

    def fills(self, ref):
        return [f for f in self.account.fills if f.ref == ref]


@pytest.fixture
def account():
    return Account()
