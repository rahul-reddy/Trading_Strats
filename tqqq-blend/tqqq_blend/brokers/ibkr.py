"""Interactive Brokers via ib_async (maintained successor of ib_insync).

Prereqs: TWS or IB Gateway running and logged in, API enabled
(Configure > API > Settings: "Enable ActiveX and Socket Clients", trusted IP 127.0.0.1,
uncheck "Read-Only API"). Paper ports: TWS 7497 / Gateway 4002. Live: 7496 / 4001.
Also needs a US equities market-data subscription for live quotes (delayed data is used as fallback).
"""
from __future__ import annotations

import math

from .base import Order


class IBKRBroker:
    name = "ibkr"

    def __init__(self, cfg):
        self.cfg = cfg.broker
        self.ib = None
        self._contracts = {}

    def connect(self):
        from ib_async import IB
        self.ib = IB()
        self.ib.connect(self.cfg.ibkr_host, self.cfg.ibkr_port, clientId=self.cfg.ibkr_client_id, timeout=15)
        self.ib.reqMarketDataType(3)  # 3 = delayed if no live subscription; live data is used when available

    def disconnect(self):
        if self.ib:
            self.ib.disconnect()

    def _contract(self, symbol):
        if symbol not in self._contracts:
            from ib_async import Stock
            c = Stock(symbol, "SMART", "USD")
            self.ib.qualifyContracts(c)
            self._contracts[symbol] = c
        return self._contracts[symbol]

    def _account(self):
        return self.cfg.ibkr_account or self.ib.managedAccounts()[0]

    def net_liquidation(self) -> float:
        for v in self.ib.accountSummary(self._account()):
            if v.tag == "NetLiquidation" and v.currency == "USD":
                return float(v.value)
        raise RuntimeError("NetLiquidation not found in IBKR account summary")

    def positions(self) -> dict[str, int]:
        acct = self._account()
        return {p.contract.symbol: int(p.position) for p in self.ib.positions(acct) if p.position}

    def last_price(self, symbol: str) -> float:
        [t] = self.ib.reqTickers(self._contract(symbol))
        px = t.marketPrice()
        if px is None or math.isnan(px):
            px = t.close
        if px is None or math.isnan(px) or px <= 0:
            raise RuntimeError(f"No usable IBKR price for {symbol}")
        return float(px)

    def place(self, order: Order) -> str:
        from ib_async import MarketOrder, Order as IBOrder
        if order.order_type == "MOC":
            o = IBOrder(action=order.side, totalQuantity=order.quantity, orderType="MOC")
        else:
            o = MarketOrder(order.side, order.quantity)
        o.account = self._account()
        o.tif = "DAY"
        trade = self.ib.placeOrder(self._contract(order.symbol), o)
        self.ib.sleep(2)
        return f"ibkr#{trade.order.orderId} {trade.orderStatus.status}"
