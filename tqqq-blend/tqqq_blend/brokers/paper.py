"""Local paper broker: fills instantly at the live price, persisted to state/paper_account.json.
Use it to forward-test the full daily process before connecting a real account."""
from __future__ import annotations

import json
from pathlib import Path

import yfinance as yf

from .base import Order


class PaperBroker:
    name = "paper"

    def __init__(self, cfg):
        self.path = Path(cfg.state_dir) / "paper_account.json"
        self.start_cash = cfg.risk.capital
        self.acct = None

    def connect(self):
        if self.path.exists():
            self.acct = json.loads(self.path.read_text())
        else:
            self.acct = {"cash": self.start_cash, "positions": {}}

    def disconnect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.acct, indent=2))

    def last_price(self, symbol: str) -> float:
        return float(yf.Ticker(symbol).fast_info["last_price"])

    def positions(self) -> dict[str, int]:
        return {k: v for k, v in self.acct["positions"].items() if v}

    def net_liquidation(self) -> float:
        return self.acct["cash"] + sum(q * self.last_price(s) for s, q in self.positions().items())

    def place(self, order: Order) -> str:
        px = self.last_price(order.symbol)
        self.acct["cash"] -= order.signed_qty * px
        self.acct["positions"][order.symbol] = self.acct["positions"].get(order.symbol, 0) + order.signed_qty
        return f"paper-fill@{px:.2f}"
