"""Interactive Brokers via ib_async (maintained successor of ib_insync).

Prereqs: TWS or IB Gateway running and logged in, API enabled
(Configure > API > Settings: "Enable ActiveX and Socket Clients", trusted IP 127.0.0.1,
uncheck "Read-Only API"). Paper ports: TWS 7497 / Gateway 4002. Live: 7496 / 4001.
Also needs a US equities market-data subscription for live quotes (delayed data is used as fallback).

On connect the account is checked against `ibkr_account_type`: paper account IDs start with "D"
(DU1234567), live ones don't. A mismatch disconnects and raises, so a port mix-up can't send orders
meant for the paper account to a live one.
"""

from __future__ import annotations

import math

from .base import Fill, Order

# TWS / IB Gateway ports, plus 4004 / 4003 used by the ib-gateway Docker image (deploy/docker-compose.yml)
PAPER_PORTS, LIVE_PORTS = {7497, 4002, 4004}, {7496, 4001, 4003}
DATA_TYPES = {1: "live", 2: "frozen", 3: "delayed", 4: "delayed-frozen"}


class IBKRBroker:
    name = "ibkr"
    reports_fills = True  # fills() works, so bucket ledgers can track this broker's orders

    def __init__(self, cfg):
        self.cfg = cfg.broker
        self.ib = None
        self.account_type = None
        self._contracts = {}
        self.commission_missing = 0  # fills() whose commission report never arrived

    def connect(self, readonly: bool = False, client_id: int | None = None):
        from ib_async import IB

        want = self.cfg.ibkr_account_type.lower()
        if want not in ("paper", "live"):
            raise ValueError(f"ibkr_account_type must be 'paper' or 'live', not '{want}'")
        if self.cfg.ibkr_port in (LIVE_PORTS if want == "paper" else PAPER_PORTS):
            raise ValueError(
                f"ibkr_port {self.cfg.ibkr_port} is a standard {'live' if want == 'paper' else 'paper'} "
                f"port but ibkr_account_type is '{want}'"
            )
        self.ib = IB()
        self.ib.connect(
            self.cfg.ibkr_host,
            self.cfg.ibkr_port,
            timeout=15,
            readonly=readonly,
            clientId=self.cfg.ibkr_client_id if client_id is None else client_id,
        )
        acct, on_login = self._account(), self.ib.managedAccounts()
        if acct not in on_login:
            self.disconnect()
            raise RuntimeError(f"Account {acct} is not on this login (it has {on_login})")
        got = "paper" if acct.upper().startswith("D") else "live"
        if got != want:
            self.disconnect()
            raise RuntimeError(f"Connected to a {got} account ({acct}) but ibkr_account_type is '{want}'")
        self.account_type = got
        self.ib.reqMarketDataType(3)  # 3 = delayed if no live subscription; live data is used when available

    def disconnect(self):
        if self.ib and self.ib.isConnected():
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
        from ib_async import MarketOrder
        from ib_async import Order as IBOrder

        if order.order_type == "MOC":
            o = IBOrder(action=order.side, totalQuantity=order.quantity, orderType="MOC")
        else:
            o = MarketOrder(order.side, order.quantity)
        o.account = self._account()
        o.tif = "DAY"
        o.orderRef = order.ref  # the bucket's tag: fills() credits executions back to that bucket only
        trade = self.ib.placeOrder(self._contract(order.symbol), o)
        self.ib.sleep(2)
        return f"ibkr#{trade.order.orderId} {trade.orderStatus.status}"

    def fills(self, ref: str) -> list[Fill]:
        """Today's executions for this account that carry `ref` (IB returns the current day's executions).
        Commission reports arrive separately, after their executions, so this waits up to about 5 seconds for
        them. One still missing counts as 0 and is counted in `commission_missing`, which the reconcile records."""
        from ib_async import ExecutionFilter

        found = [
            f for f in self.ib.reqExecutions(ExecutionFilter(acctCode=self._account())) if f.execution.orderRef == ref
        ]

        def reported(f) -> bool:
            c = f.commissionReport
            return bool(c and c.execId == f.execution.execId)

        for _ in range(10):
            if all(reported(f) for f in found):
                break
            self.ib.sleep(0.5)
        later = {f.execution.execId: f for f in self.ib.fills()}  # reports that attached to the session's copy
        out = []
        self.commission_missing = 0
        for f in found:
            e = f.execution
            src = f if reported(f) else later.get(e.execId, f)
            fee = src.commissionReport.commission if reported(src) else None
            if fee is None or not math.isfinite(fee):
                self.commission_missing += 1
                fee = 0.0
            out.append(
                Fill(
                    exec_id=e.execId,
                    ref=e.orderRef,
                    symbol=f.contract.symbol,
                    shares=int(e.shares) if e.side == "BOT" else -int(e.shares),
                    price=float(e.price),
                    commission=float(fee),
                    time=e.time.isoformat() if hasattr(e.time, "isoformat") else str(e.time),
                )
            )
        return out

    def describe(self) -> dict:
        """Read-only snapshot for `ibkr-check`: account, balances, positions, open orders, quote quality."""
        acct = self._account()
        summary = {v.tag: v.value for v in self.ib.accountSummary(acct) if v.currency in ("USD", "")}
        quotes = {}
        for sym in ("SPY", "SGOV"):
            [t] = self.ib.reqTickers(self._contract(sym))
            quotes[sym] = {
                "price": t.marketPrice(),
                "bid": t.bid,
                "ask": t.ask,
                "prev_close": t.close,
                "data": DATA_TYPES.get(getattr(t, "marketDataType", 0), "unknown"),
            }
        return {
            "accounts_on_login": self.ib.managedAccounts(),
            "account": acct,
            "account_type": self.account_type,
            "net_liquidation": summary.get("NetLiquidation"),
            "cash": summary.get("TotalCashValue"),
            "buying_power": summary.get("BuyingPower"),
            "positions": {p.contract.symbol: p.position for p in self.ib.positions(acct) if p.position},
            "open_orders": [
                f"{t.order.action} {t.order.totalQuantity} {t.contract.symbol} {t.order.orderType}"
                for t in self.ib.openTrades()
            ],
            "quotes": quotes,
        }
