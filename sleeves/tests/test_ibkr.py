"""IBKRBroker's bucket plumbing against a stub of ib_async's IB object: orders carry the bucket's orderRef,
and fills() returns only that bucket's executions, signed, with their commission."""

from datetime import UTC, datetime
from types import SimpleNamespace as NS

from sleeves.brokers import Order
from sleeves.brokers.ibkr import IBKRBroker
from sleeves.config import Config


class StubIB:
    def __init__(self, executions):
        self.executions = executions
        self.placed = []

    def placeOrder(self, contract, order):  # noqa: N802 - ib_async's name
        self.placed.append(order)
        return NS(order=NS(orderId=7), orderStatus=NS(status="PreSubmitted"))

    def reqExecutions(self, flt):  # noqa: N802
        return self.executions

    def sleep(self, s):
        pass

    def managedAccounts(self):  # noqa: N802
        return ["DU0000000"]


def execution(exec_id, ref, symbol, side, shares, price, fee):
    when = datetime(2026, 10, 27, 20, tzinfo=UTC)
    e = NS(execId=exec_id, orderRef=ref, side=side, shares=shares, price=price, time=when)
    return NS(contract=NS(symbol=symbol), execution=e, commissionReport=NS(commission=fee) if fee is not None else None)


def broker(ib):
    b = IBKRBroker(Config())
    b.ib = ib
    b._contracts = {"TQQQ": object()}
    return b


def test_place_tags_the_order_with_the_bucket():
    ib = StubIB([])
    broker(ib).place(Order("TQQQ", "BUY", 5, "MOC", "tqqq-blend/paper-mix"))
    assert ib.placed[0].orderRef == "tqqq-blend/paper-mix" and ib.placed[0].orderType == "MOC"


def test_fills_returns_only_this_buckets_executions():
    ib = StubIB(
        [
            execution("e1", "tqqq-blend/paper", "TQQQ", "BOT", 10, 50.0, 1.25),
            execution("e2", "tqqq-blend/paper-mix", "TQQQ", "BOT", 4, 50.1, 1.0),
            execution("e3", "tqqq-blend/paper", "SQQQ", "SLD", 3, 20.0, None),  # commission not in yet
            execution("e4", "", "TQQQ", "BOT", 99, 50.0, 1.0),  # a manual trade: no bucket
        ]
    )
    got = broker(ib).fills("tqqq-blend/paper")
    assert [(f.exec_id, f.symbol, f.shares, f.price, f.commission) for f in got] == [
        ("e1", "TQQQ", 10, 50.0, 1.25),
        ("e3", "SQQQ", -3, 20.0, 0.0),
    ]
    assert got[0].time.startswith("2026-10-27T20:00")
