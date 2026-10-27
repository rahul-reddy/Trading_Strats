# Sleeves: specification

Three long-only strategies that each run as their own **bucket** (own capital, own ledger, own dashboard
card) in a shared broker account. Together with a 20% tqqq-blend bucket they make the owner's $100k "mix"
paper portfolio, split 20% tqqq-blend / 30% rotation / 25% sectors / 25% quality (owner, 2026-10-26).

All three decide **once a month, at the last NYSE trading day's close**, and trade near the close with
market-on-close orders. Money a rule leaves empty sits in **SGOV** (0–3 month T-bills). Every rule is in
[sleeves/rules.py](sleeves/rules.py); the backtest calls the same functions.

## 1. ETF rotation (30%)

A retail version of multi-asset trend following (managed-futures funds; Hurst, Ooi and Pedersen, *A
Century of Evidence on Trend-Following Investing*, AQR 2017).

- **Universe (12):** SPY, QQQ, IWM, EFA, EEM, VNQ, TLT, IEF, GLD, DBC, LQD, HYG.
- **Score:** the average of the 1-, 3-, 6- and 12-month returns (21/63/126/252 trading days).
- **Eligible:** price above its 210-day (about 10-month) average **and** a positive score.
- **Hold:** the 4 best eligible at 25% each. Each slot left empty goes to SGOV.

## 2. SPDR sector momentum (25%)

Cross-sectional momentum (Jegadeesh and Titman 1993; Asness, Moskowitz and Pedersen, *Value and Momentum
Everywhere*, 2013) applied to sectors instead of single stocks, with a market filter against momentum
crashes (Daniel and Moskowitz, *Momentum Crashes*, 2016).

- **Universe (11):** XLK, XLF, XLE, XLV, XLI, XLY, XLP, XLU, XLB, XLC, XLRE (an ETF counts once it has 12
  months of history: XLC and XLRE are younger).
- **Momentum:** the 12-month return skipping the latest month (price 21 days ago / price 252 days ago − 1).
- **Market filter:** SPY's close above the average of its last 10 month-end closes.
- **Hold:** filter on → the 3 best sectors at 1/3 each. Filter off → 100% SGOV.

Sector ETFs instead of 25 single stocks: similar results (below), it can be backtested exactly as it is
traded, and the sector cap the owner asked for is built in (at most a third in any sector).

## 3. Quality-value (25%)

Cheap and profitable stocks (Novy-Marx, *The Other Side of Value*, 2013; Fama and French's profitability
factor; Greenblatt's Magic Formula), with the same market filter.

- **Universe:** the S&P 500, from the Finviz Elite screen (sector, price-to-book, return on equity,
  operating margin). No screen, no trade.
- **Screen:** positive book value and operating margin; cheapest 40% by price-to-book **and** top 40% by
  return on equity; ranked by the sum of the two ranks; at most 5 per sector; at most 25 names; equal weight.
- **Rebalance:** once a year (12 months after the last one), at the first month-end, or when the market
  filter turns. Filter off → 100% SGOV.

## Execution and risk

- **When:** every weekday 5–30 minutes before the close the bucket values itself; at a month-end (or the
  first run after a missed one) it decides. A new bucket waits for its first month-end.
- **Orders:** whole shares toward the targets, 99% of equity invested (1% cash for MOC slippage). A holding
  that stays is resized only if its weight is off by more than 2 points. Sells first.
- **Breakers:** drawdown halt (rotation 30%, sectors 40%, quality 50% from the bucket's peak), a 8% one-day
  move in SPY stops the run, `max_order_value`, the kill switch, once-per-day, `--live` +
  `live_trading_enabled`, and the account-type check. Stale or missing data stops the run.
- **Ledger:** each bucket owns only the shares its own fills bought (docs/BUCKETS.md).

## Backtests

`python -m sleeves backtest --kind rotation|sectors`: the live rules on daily dividend-adjusted closes,
decided at a month-end close and held from the next day, 10 bps per side, cash at the T-bill rate.

**Rotation** (2008-04 to 2026-10; history starts when all 12 ETFs have a year of data):

| | CAGR | Vol | Max DD | Sharpe | Worst 12m |
|---|---|---|---|---|---|
| Rotation, top 4 (as configured) | 9.5% | 12.8% | −18.4% | 0.77 | −17.4% |
| Top 3 | 9.6% | 13.9% | −18.5% | 0.73 | −17.3% |
| Top 5 | 10.1% | 11.8% | −14.4% | 0.87 | −13.4% |
| SPY buy and hold | 11.7% | 19.7% | −51.5% | 0.66 | −36.1% |
| First half, top 4 / SPY | 7.7% / 8.7% | | −18.4% / −51.5% | 0.64 / 0.51 | |
| Second half, top 4 / SPY | 11.4% / 14.7% | | −17.3% / −33.7% | 0.91 / 0.83 | |

**Sector momentum** (2000-01 to 2026-10):

| | CAGR | Vol | Max DD | Sharpe | Worst 12m |
|---|---|---|---|---|---|
| Sectors, top 3 (as configured) | 7.9% | 13.4% | −24.8% | 0.64 | −16.3% |
| Top 2 | 8.8% | 14.8% | −25.7% | 0.64 | −15.6% |
| Top 4 | 8.4% | 12.8% | −24.2% | 0.69 | −15.9% |
| SPY buy and hold | 8.6% | 19.2% | −55.2% | 0.52 | −47.4% |
| First half, top 3 / SPY | 8.9% / 3.3% | | −24.8% / −55.2% | 0.70 / 0.26 | |
| Second half, top 3 / SPY | 7.1% / 14.2% | | −22.3% / −33.7% | 0.58 / 0.87 | |

**Quality-value** can't be backtested this way: it needs point-in-time fundamentals for every S&P 500
stock, which no free source has. The proxy is the Fama-French CRSP portfolios of stocks in the top two
book-to-market and top two operating-profitability quintiles (value-weighted, 0.5%/year cost), with the
same market filter, 1963-07 to 2026-08: **12.7% CAGR, −36.5% max drawdown, Sharpe 0.84** (market: 10.9%,
−50.3%, 0.75). From 2007: 8.5%, −35.5%. The live screen holds about 20–25 names, not hundreds, so expect it
to swing more than the proxy.

**The mix** (monthly rebalanced, 2008-05 to 2026-08; tqqq-blend from its own backtest, quality from the
proxy): 20/30/25/25 gave **11.1% CAGR, 11.6% vol, −17.9% max drawdown, Sharpe 0.96**; SPY 11.8%, 15.7%,
−46.3%, 0.80. 2017-07 to 2026-08: 13.0% vs SPY's 15.2%.

## Limitations

- These are published factors; McLean and Pontiff (2016) found such returns fall by about half after
  publication. Expect less than the backtests.
- In strong bull markets all three trail the S&P 500 (second halves above). What they buy is smaller
  drawdowns.
- Top 5 was better than the configured top 4 for rotation in both halves. It was not switched after seeing
  that: 4 was chosen before this backtest, and picking the best of a grid on the same data overstates it.
- The quality backtest is a proxy (above). The tqqq-blend backtest is in-sample.
- Fills are market-on-close at a price the run can't know; the ledger records the real fills.
