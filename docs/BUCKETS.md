# Buckets: several strategies in one broker account

The owner tests each strategy in its own **bucket**: its own capital, shares, performance and dashboard
card, even when several buckets trade the same symbols in the same account. The IBKR paper account holds,
for example, the $100k tqqq-blend bucket and the $100k "mix" portfolio's four buckets (20% tqqq-blend,
30% ETF rotation, 25% sector momentum, 25% quality-value, the last three in sleeves/), and two of those
trade TQQQ. The dashboard shows each bucket, and the mix as one portfolio (the sum of its buckets).

So no bucket may treat the account's positions as its own. Each bucket owns exactly the shares its own
fills bought, and the account must always hold what all the buckets' ledgers add up to.

## How it works

1. **Tag.** Every order carries the bucket's ID as its IBKR `orderRef` (`bucket` in the profile, e.g.
   `tqqq-blend/paper-mix`). IDs are unique per account.
2. **Pending.** A placed order is recorded in the bucket's `pending_orders` before anything else can fail.
3. **Reconcile.** At 16:10 ET (`trading-reconcile.timer`), after the 16:00 MOC fills, `reconcile` reads
   the day's executions, credits those tagged with the bucket to its ledger (shares, and cash including
   commission), and drops the pending orders they finish. A market order is credited at once, in the run.
   Each execution is applied once (`applied_fills`), so re-running a reconcile changes nothing.
4. **Check.** Before a bucket trades, it reads every bucket ledger on the same `account_group` (each
   `state/<bucket>/state.json` under the shared state root) and requires the account's position in each
   symbol any of them holds, or that this bucket trades, to equal the ledgers' sum, give or take orders
   placed today that may already have filled. Any other difference stops the run with an error naming the
   symbols. `account_check = "at_least"` (the live profile) allows the owner's own shares on top.
5. **Stale orders stop the run.** IB reports only the current day's executions, so an order that was
   never reconciled (the server stopped, the reconcile failed) can't be matched later. The bucket's next
   run refuses to trade until a person fixes the ledger.

Brand-new buckets start with `risk.capital` in cash and no shares. A bucket from before ledgers (one with
`strategy_cash` but no `positions`) that has placed real orders is seeded once from the account's
positions in its own symbols, only while no other bucket holds them. One that only ever dry-ran starts
with no shares, whatever the account holds.

## The state.json fields every strategy shares

Other strategy folders read these fields, so they are a contract. Change them in every strategy at once.

| Field | Meaning |
|---|---|
| `account_group` | Which account the bucket shares, e.g. `ibkr-paper`. Buckets match on this string. |
| `bucket` | The order tag (IBKR `orderRef`). |
| `strategy_cash` | Cash the bucket owns. |
| `positions` | `{symbol: shares}` it owns, changed only by its own fills. |
| `pending_orders` | `[{date, symbol, side, qty, result}]`, placed and not yet matched to fills. |
| `applied_fills` | Execution IDs already credited. |

Each bucket also writes `fills.csv` (every credited execution) and `reconcile.jsonl` (every reconcile).

## Fixing a ledger by hand

A run that stops with "account positions don't match the bucket ledgers" or "orders from an earlier day
were never matched to fills" has placed nothing. To fix it:

1. Stop the timers from acting while you work: `touch /opt/trading/state/<bucket>/STOP` for each bucket on
   the account.
2. In IBKR (Client Portal → Trade log, or `ibkr-check`), find what really filled for each pending order.
   The `orderRef` column names the bucket.
3. Edit `/opt/trading/state/<bucket>/state.json`: add the filled shares to `positions`, subtract their
   cost and commission from `strategy_cash`, and delete the finished entries from `pending_orders`. Add a
   line to `fills.csv` for each execution, so the history stays complete.
4. A manual trade in a managed symbol: undo it in IBKR, or, if it was meant to belong to a bucket, add it to
   that bucket's ledger the same way.
5. Check with `docker compose run --rm <service> run` (a dry run): it must not report a mismatch. Then
   remove the STOP files.
