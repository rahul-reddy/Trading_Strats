"""Charles Schwab via schwab-py (Schwab Trader API).

Prereqs: an approved app at developer.schwab.com ("Accounts and Trading Production"), with the
callback URL matching config (default https://127.0.0.1:8182). Put the keys in env vars
SCHWAB_API_KEY and SCHWAB_APP_SECRET. Run `python -m tqqq_blend schwab-login` once to create the
token file. Schwab refresh tokens expire after 7 days, so re-run the login weekly.
"""

from __future__ import annotations

import os

from .base import Fill, Order


class SchwabBroker:
    name = "schwab"

    def __init__(self, cfg):
        self.cfg = cfg.broker
        self.client = None
        self.account_hash = None

    def connect(self, interactive: bool = False):
        from schwab import auth

        key, secret = os.environ.get("SCHWAB_API_KEY"), os.environ.get("SCHWAB_APP_SECRET")
        if not key or not secret:
            raise RuntimeError("Set SCHWAB_API_KEY and SCHWAB_APP_SECRET environment variables")
        if interactive:
            self.client = auth.client_from_login_flow(
                key, secret, self.cfg.schwab_callback_url, self.cfg.schwab_token_path
            )
        else:
            self.client = auth.client_from_token_file(self.cfg.schwab_token_path, key, secret)
        accts = self.client.get_account_numbers()
        accts.raise_for_status()
        accts = accts.json()
        self.account_hash = self.cfg.schwab_account_hash or accts[0]["hashValue"]

    def disconnect(self):
        pass

    def _account(self) -> dict:
        r = self.client.get_account(self.account_hash, fields=[self.client.Account.Fields.POSITIONS])
        r.raise_for_status()
        return r.json()["securitiesAccount"]

    def net_liquidation(self) -> float:
        return float(self._account()["currentBalances"]["liquidationValue"])

    def positions(self) -> dict[str, int]:
        out = {}
        for p in self._account().get("positions", []):
            qty = int(p.get("longQuantity", 0) - p.get("shortQuantity", 0))
            if qty:
                out[p["instrument"]["symbol"]] = qty
        return out

    def last_price(self, symbol: str) -> float:
        r = self.client.get_quote(symbol)
        r.raise_for_status()
        return float(r.json()[symbol]["quote"]["lastPrice"])

    def place(self, order: Order) -> str:
        from schwab.orders.common import Duration, OrderType, Session
        from schwab.orders.equities import equity_buy_market, equity_sell_market

        spec = (equity_buy_market if order.side == "BUY" else equity_sell_market)(order.symbol, order.quantity)
        spec.set_duration(Duration.DAY).set_session(Session.NORMAL)
        if order.order_type == "MOC":
            spec.set_order_type(OrderType.MARKET_ON_CLOSE)
        r = self.client.place_order(self.account_hash, spec)
        r.raise_for_status()
        from schwab.utils import Utils

        return f"schwab#{Utils(self.client, self.account_hash).extract_order_id(r)}"

    def fills(self, ref: str) -> list[Fill]:
        # A bucket's shares come only from its own fills (ledger.py). Schwab has no orderRef equivalent wired
        # up here, so a Schwab run refuses rather than guess which shares in the account are this bucket's.
        raise NotImplementedError("Schwab fill reports are not implemented; bucket ledgers need them (ledger.py)")
