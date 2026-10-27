"""Bucket ledgers: several strategies (or several copies of one) share a broker account, each with its own
capital, so none of them may treat the account's positions as its own.

Each bucket keeps, in its state.json:
  account_group   which broker account it shares (e.g. "ibkr-paper"); buckets match on this string
  bucket          its order tag (IBKR orderRef), e.g. "tqqq-blend/paper"
  strategy_cash   cash it owns
  positions       {symbol: shares} it owns. Changed only by fills tagged with its bucket
  pending_orders  orders placed but not yet matched to fills
  applied_fills   execution IDs already applied, so applying a fill twice does nothing

Every state.json one level under the shared state root (state/<bucket>/state.json) with the same
account_group is a sibling. Before a bucket trades, the account must hold exactly what the buckets' ledgers
add up to, give or take orders still in flight (check_account). Anything else, such as a manual trade, an
unreconciled fill or an old-format sibling, stops the run instead of trading on positions nobody can explain.
This file format is shared with the other strategy folders (docs/BUCKETS.md); keep them in step.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from .brokers import Fill, Order

APPLIED_KEEP = 2000  # execution IDs remembered per bucket; far more than a year of daily orders


class LedgerError(RuntimeError):
    """The bucket's shares can't be established; the run must not trade."""


def positions(data: dict) -> dict[str, int]:
    return {k: int(v) for k, v in data.get("positions", {}).items() if int(v)}


def apply_fills(data: dict, fills: list[Fill]) -> list[Fill]:
    """Credit this bucket's fills to its ledger, each at most once. Returns the newly applied ones."""
    applied = data.setdefault("applied_fills", [])
    seen = set(applied)
    pos = data.setdefault("positions", {})
    new = []
    for f in sorted(fills, key=lambda x: (x.time, x.exec_id)):
        if f.exec_id in seen or f.ref != data.get("bucket"):
            continue
        pos[f.symbol] = int(pos.get(f.symbol, 0)) + f.shares
        if not pos[f.symbol]:
            del pos[f.symbol]
        data["strategy_cash"] = data["strategy_cash"] - f.shares * f.price - f.commission
        applied.append(f.exec_id)
        seen.add(f.exec_id)
        new.append(f)
    del applied[:-APPLIED_KEEP]
    return new


def record_pending(data: dict, orders: list[Order], day: date, results: list[str]) -> None:
    pending = data.setdefault("pending_orders", [])
    for o, result in zip(orders, results, strict=True):
        pending.append({"date": str(day), "symbol": o.symbol, "side": o.side, "qty": o.quantity, "result": result})


def settle_pending(data: dict, fills: list[Fill], today: date, after_close: bool) -> list[dict]:
    """Drop pending orders that are finished. An order is finished once fills for its day cover its quantity,
    or, after today's close, whatever filled today (an MOC or day order can't fill later). An order from an
    earlier day is never settled here: today's executions can't show its fills (see stale_pending).
    Returns the finished orders, each with how much filled, so a short fill is recorded, not forgotten."""
    done, keep = [], []
    filled: dict[tuple[str, str], int] = {}
    for f in fills:
        filled[(f.time[:10], f.symbol)] = filled.get((f.time[:10], f.symbol), 0) + f.shares
    for p in data.get("pending_orders", []):
        want = p["qty"] if p["side"] == "BUY" else -p["qty"]
        got = filled.get((p["date"], p["symbol"]), 0)
        if got == want or (after_close and p["date"] == str(today)):
            done.append(p | {"filled": got})
        else:
            keep.append(p)
    data["pending_orders"] = keep
    return done


def stale_pending(data: dict, today: date) -> list[dict]:
    """Pending orders from an earlier day. IB reports only the current day's executions, so these can no
    longer be matched to fills automatically: the ledger can't be trusted until someone checks them."""
    return [p for p in data.get("pending_orders", []) if p["date"] < str(today)]


def siblings(state_dir: Path, account_group: str) -> dict[Path, dict]:
    """Every bucket state.json under the shared state root that is on the same account, this one included."""
    out = {}
    for p in sorted(state_dir.resolve().parent.glob("*/state.json")):
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError) as e:
            raise LedgerError(f"can't read bucket ledger {p}: {e}") from e
        if d.get("account_group") == account_group:
            out[p.parent] = d
    return out


def check_account(
    state_dir: Path,
    account_group: str,
    broker_positions: dict[str, int],
    symbols: set[str],
    mine: dict,
    today: date,
    mode: str = "exact",
) -> None:
    """Stop unless, for every symbol any bucket on the account holds or this bucket may trade, the account's
    position equals the sum of the buckets' ledgers, up to orders placed today that may already have filled.
    An order from an earlier day that is still pending widens nothing: its fills are unknown, so a difference
    it might explain stops the run. mode "at_least" only requires the account to hold at least that much
    (the owner's own shares on top).

    `mine` is this bucket's in-memory state (it may be newer than its file). Raises LedgerError."""
    if mode not in ("exact", "at_least"):
        raise ValueError(f"account_check must be 'exact' or 'at_least', not '{mode}'")
    books = siblings(state_dir, account_group)
    books[state_dir.resolve()] = mine
    legacy = [str(p.name) for p, d in books.items() if "positions" not in d]
    if legacy:
        raise LedgerError(f"bucket(s) {legacy} on {account_group} have no share ledger yet; run them once first")
    held: dict[str, int] = {}
    low: dict[str, int] = {}
    high: dict[str, int] = {}
    for d in books.values():
        for sym, qty in positions(d).items():
            held[sym] = held.get(sym, 0) + qty
        for p in d.get("pending_orders", []):
            if p["date"] != str(today):
                continue
            q = p["qty"] if p["side"] == "BUY" else -p["qty"]
            (high if q > 0 else low)[p["symbol"]] = (high if q > 0 else low).get(p["symbol"], 0) + q
    problems = []
    for sym in sorted(set(held) | symbols):
        base, have = held.get(sym, 0), broker_positions.get(sym, 0)
        top = float("inf") if mode == "at_least" else base + high.get(sym, 0)
        if not base + low.get(sym, 0) <= have <= top:
            problems.append(f"{sym}: account holds {have}, bucket ledgers add up to {base}")
    if problems:
        raise LedgerError(
            "account positions don't match the bucket ledgers on "
            + account_group
            + " ("
            + "; ".join(problems)
            + "). A manual trade or an unreconciled fill? Fix the ledgers before trading (docs/BUCKETS.md)."
        )


def has_real_orders(state_dir: Path) -> bool:
    """Whether this bucket ever sent an order (trades.csv rows other than dry runs)."""
    p = state_dir / "trades.csv"
    if not p.exists():
        return False
    with open(p, newline="", encoding="utf-8") as f:
        return any(r.get("result") != "DRY-RUN" for r in csv.DictReader(f))


def seed_from_broker(state_dir: Path, data: dict, broker_positions: dict[str, int], symbols: set[str]) -> None:
    """One-time migration for a bucket that predates ledgers and has traded: it owned whatever the account held
    in its own symbols. Allowed only while no other bucket on the account holds any of those symbols. A bucket
    that only ever dry-ran owns nothing, whatever the account holds (the owner's own shares are not its)."""
    if not has_real_orders(state_dir):
        data["positions"] = {}
        return
    others = {p: d for p, d in siblings(state_dir, data["account_group"]).items() if p != state_dir.resolve()}
    clash = sorted({s for d in others.values() for s in positions(d)} & symbols)
    if clash:
        raise LedgerError(f"can't seed {data.get('bucket')} from the account: other buckets already hold {clash}")
    data["positions"] = {s: q for s, q in broker_positions.items() if s in symbols and q}
