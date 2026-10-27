"""Local paper broker: fills instantly at the live price, persisted to state/paper_account.json.
Use it to forward-test the full daily process before connecting a real account."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import yfinance as yf

from .base import Fill, Order


class PaperBroker:
    name = "paper"
    reports_fills = True  # fills() works, so bucket ledgers can track this broker's orders

    def __init__(self, cfg):
        self.path = Path(cfg.state_dir) / "paper_account.json"
        self.start_cash = cfg.risk.capital
        self.acct = None

    def connect(self):
        if self.path.exists():
            self.acct = json.loads(self.path.read_text())
        else:
            self.acct = {"cash": self.start_cash, "positions": {}}
        self.acct.setdefault("fills", [])

    def disconnect(self):
        if self.acct is None:  # connect() failed: never overwrite the account file with nothing
            return
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
        exec_id = f"paper-{len(self.acct['fills']) + 1}"
        self.acct["fills"].append(
            {
                "exec_id": exec_id,
                "ref": order.ref,
                "symbol": order.symbol,
                "shares": order.signed_qty,
                "price": px,
                "commission": 0.0,
                "time": datetime.now().isoformat(timespec="seconds"),
            }
        )
        return f"paper-fill@{px:.2f}"

    def fills(self, ref: str) -> list[Fill]:
        return [Fill(**f) for f in self.acct["fills"] if f["ref"] == ref]
