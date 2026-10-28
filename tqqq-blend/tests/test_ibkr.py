"""IBKRBroker's bucket plumbing against a stub of ib_async's IB object: orders carry the bucket's orderRef,
and fills() returns only that bucket's executions, signed, with their commission."""

from datetime import UTC, datetime
from types import SimpleNamespace as NS

from tqqq_blend.brokers import Order
from tqqq_blend.brokers.ibkr import IBKRBroker
from tqqq_blend.config import Config


class StubIB:
    def __init__(self, executions, session_fills=(), arrive_after=None):
        self.executions = executions
        self.session_fills = list(session_fills)
        self.arrive_after = arrive_after or {}  # exec_id -> (sleeps before its report arrives, commission)
        self.placed = []
        self.sleeps = 0

    def placeOrder(self, contract, order):  # noqa: N802 - ib_async's name
        self.placed.append(order)
        return NS(order=NS(orderId=7), orderStatus=NS(status="PreSubmitted"))

    def reqExecutions(self, flt):  # noqa: N802
        return self.executions

    def sleep(self, s):
        self.sleeps += 1
        for f in self.executions:
            n, fee = self.arrive_after.get(f.execution.execId, (None, None))
            if n is not None and self.sleeps >= n:
                f.commissionReport = NS(execId=f.execution.execId, commission=fee)

    def fills(self):
        return self.session_fills

    def managedAccounts(self):  # noqa: N802
        return ["DU0000000"]


def execution(exec_id, ref, symbol, side, shares, price, fee):
    when = datetime(2026, 10, 27, 20, tzinfo=UTC)
    e = NS(execId=exec_id, orderRef=ref, side=side, shares=shares, price=price, time=when)
    report = NS(execId=exec_id, commission=fee) if fee is not None else NS(execId="", commission=0.0)
    return NS(contract=NS(symbol=symbol), execution=e, commissionReport=report)


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


def test_a_commission_report_that_arrives_late_is_waited_for():
    late = execution("e1", "b", "TQQQ", "BOT", 5, 50.0, None)
    ib = StubIB([late], arrive_after={"e1": (3, 1.05)})
    b = broker(ib)
    [f] = b.fills("b")
    assert f.commission == 1.05 and b.commission_missing == 0 and ib.sleeps == 3


def test_a_report_on_the_sessions_copy_is_used():
    fill = execution("e1", "b", "TQQQ", "BOT", 5, 50.0, None)
    session = execution("e1", "b", "TQQQ", "BOT", 5, 50.0, 0.85)
    [f] = broker(StubIB([fill], session_fills=[session])).fills("b")
    assert f.commission == 0.85


def test_a_missing_commission_is_counted_not_hidden():
    b = broker(StubIB([execution("e1", "b", "TQQQ", "BOT", 5, 50.0, None)]))
    [f] = b.fills("b")
    assert f.commission == 0.0 and b.commission_missing == 1
