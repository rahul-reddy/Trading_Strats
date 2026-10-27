# Sleeves: how it works and how to run it

## In plain words

Once a month, at the last close of the month, each bucket looks at the market and picks what to hold for
the next month:

- **ETF rotation** ranks 12 ETFs (US and foreign stocks, real estate, bonds, gold, commodities) by their
  recent returns, keeps only those in an uptrend, and holds the best 4. When fewer than 4 qualify, the rest
  waits in T-bills. In 2008 it was mostly in bonds and gold.
- **Sector momentum** holds the 3 sector ETFs with the best year (skipping the last month), but only while
  the S&P 500 is above its 10-month average. Otherwise it waits in T-bills.
- **Quality-value** holds about 20–25 S&P 500 stocks that are both cheap (low price-to-book) and profitable
  (high return on equity), at most 5 per sector, and rebuilds the list once a year. The same market filter
  moves it to T-bills.

On every other weekday each bucket only values itself, so the dashboard has a daily point.

## Operating it

| When | What to do | Where |
|---|---|---|
| Month-end, after the close | Check each bucket's run says `executed` or `no change`, and the 16:10 reconcile says `reconciled` | dashboard, `runs.jsonl`, `reconcile.jsonl` |
| Daily | Glance at the dashboard: `hold` is the normal daily status | |
| A run says `error` | It placed nothing. Read the error; data errors retry at the next firing, a missed month-end is caught up the next day | `runs.jsonl` |
| A ledger mismatch | The run placed nothing. Find what filled and fix the ledger | [docs/BUCKETS.md](../docs/BUCKETS.md) |
| Emergency | `touch STOP` in the bucket's state folder; the next run does nothing | |
| After a drawdown halt | Investigate before clearing `halted` in the bucket's `state.json` | |

## When to override (and when not to)

- **Don't** stop a bucket because it trails the S&P 500 in a bull market: that's in its backtest.
- **Do** stop and look if a month-end passes without a rebalance, if fills are far from the reference
  price, or if a bucket's drawdown is past its backtest's worst (rotation −18%, sectors −25%, quality −36%).
