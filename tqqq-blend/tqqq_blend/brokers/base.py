from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Order:
    symbol: str
    side: str        # "BUY" | "SELL"
    quantity: int
    order_type: str  # "MOC" | "MKT"

    @property
    def signed_qty(self) -> int:
        return self.quantity if self.side == "BUY" else -self.quantity


class Broker(Protocol):
    name: str

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def net_liquidation(self) -> float: ...
    def positions(self) -> dict[str, int]: ...
    def last_price(self, symbol: str) -> float: ...
    def place(self, order: Order) -> str: ...


def make_broker(cfg) -> "Broker":
    name = cfg.broker.name.lower()
    if name == "paper":
        from .paper import PaperBroker
        return PaperBroker(cfg)
    if name == "ibkr":
        from .ibkr import IBKRBroker
        return IBKRBroker(cfg)
    if name == "schwab":
        from .schwab import SchwabBroker
        return SchwabBroker(cfg)
    raise ValueError(f"Unknown broker '{cfg.broker.name}' (paper | ibkr | schwab)")
