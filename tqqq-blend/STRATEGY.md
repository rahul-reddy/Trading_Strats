# TQQQ Blend: strategy specification

One decision per day, made in the last 30 minutes of the session. The signals come from **QQQ** daily bars. The orders go to **TQQQ** (3x long) or **SQQQ** (3x short), never both at once. Three sleeves each produce an exposure, and those are blended into one net exposure in [-1, +1].

```
net = (trend + mr + vibha) / 3                          (clipped to [-1, 1])
if net > 0: net *= min(1, 0.20 / QQQ 20-day realized vol)      (volatility target, v2)
if QQQ 20-day realized vol > 60%: cap net long at 0.50          (rarely binds once the vol target is on)
TQQQ weight = max(net, 0) * 1.00     SQQQ weight = max(-net, 0) * 0.50
trade only if the weight changes by more than 0.10 (or goes to/from zero)
```

## Sources

| Trader | What we know | Where from |
|---|---|---|
| **Mallik (TQQQTrader, "Whitelight")** | 7 systems on TQQQ/SQQQ only: 1 momentum long, 3 mean-reversion longs, 1 MR short, 2 momentum shorts. Trend from NDX **20- and 250-day MAs**; **distance from those MAs sets the size**; Bollinger Bands detect momentum phases (bigger size). One end-of-day decision ~10 min before the close, ~2 trades/week. Live (Collective2, Jul 2022 onward): ~22% CAGR, 37.6% max DD, Sharpe 0.59. Backtest: ~80% CAGR, ~49% DD. | KINFO interview (captured with REA from the YouTube page; the video has no captions), Collective2 listing, his X posts |
| **Vibha Jha (USIC champion)** | Buys TQQQ on a **follow-through day** (up to 100%) or **3 days of higher highs and higher lows** (25/50/75% pilot), or when it retakes the 50-day. **Sell flags**: new high on weak volume, 4–5 distribution days, 3 down days in a row, losing the 10-day on rising volume, 3 down days on rising volume with lower highs and lows, failed retests. Sells in chunks as flags stack up; exits fully below the **21-day**. Trades in the last 30–60 min. | IBD Live summary (Motley Fool forum), TraderLion, podcast summaries |

None of these traders publishes exact thresholds. The parameters below are my translation of their rules into code. They are not copies of their systems.

## Sleeve 1: Trend (Mallik momentum), weight 1/3

- **Regime** uses a 1% hysteresis band around the 250-day SMA. Bull starts when close > SMA250 × 1.01; bear starts when close < SMA250 × 0.99.
- **Bull size** depends on extension `ext = close/SMA250 − 1`:
  - ext ≤ 15% → 1.0
  - 15–25% → 0.75
  - over 25% → 0.50
  - If close < SMA20 (trend weakening), size is capped at 0.50.
  - If close > upper Bollinger band (20, 2), the market is in a momentum phase and size is 1.0.
- **Bear**: SQQQ momentum short when close < SMA20 and SMA20 is falling. **This is off by default** (`short_enabled = false`) because it lost money in testing; see the results below.

## Sleeve 2: Mean reversion (Mallik MR), weight 1/3

| Setup | Entry | Exit |
|---|---|---|
| Dip buy in uptrend | close > SMA250 and RSI(2) < 10 → +1.0 | RSI(2) > 70 or 7 days |
| Deep oversold bounce | close < SMA250, ext < −20%, RSI(2) < 5 → +0.5 | same |
| Rally fade in downtrend (SQQQ) | close < SMA250 and RSI(2) > 90 → −1.0 | RSI(2) < 30, 7 days, or close > SMA250 |

## Sleeve 3: Vibha Jha market timing, weight 1/3 (long only)

Each day, sells are evaluated first and buys second.

- **Distribution day**: down at least 0.2% on higher volume than the prior day. A distribution day counts for 25 sessions, and drops off early if price rallies 5% above it.
- **Sell flags** (count each day):
  1. New 52-week closing high on volume below its 50-day average
  2. 4 or more distribution days
  3. 3 consecutive down closes
  4. Close below the 10-day SMA on rising volume
  5. 3 down days on rising volume with lower highs and lower lows
- **Trim**: when 2 or more flags are active and a new one fires, sell 25% of the sleeve.
- **Distribution cap**: 5 or more distribution days caps the sleeve at 50%.
- **Full exit**: close below the 21-day EMA.
- **Follow-through day** (only when the sleeve is ≤ 50%): day 4 or later of a rally attempt, up at least 1.25% on higher volume. The sleeve goes to 100%. A rally attempt starts on the first up day after the low, and resets if price undercuts that low.
- **3 days of higher highs and higher lows** that also close above the open, with fewer than 4 distribution days: size 0.25, plus 0.25 if above the 21-day EMA, plus 0.25 if above the 50-day, capped at 0.75.
- **Retake of the 50-day** (while light and above the 21-day EMA): 0.50.
- **Blend rule (Mallik's regime governs Vibha's size)**: below the 250-day SMA, the sleeve is capped at 0.50. Without this cap, follow-through days in 2000–02 and 2008 failed over and over at full size, and the sleeve alone drew down 93%.

## Volatility target (v2)

After the three sleeves are blended, a positive net is multiplied by `min(1, 0.20 / rv20)`, where rv20 is QQQ's 20-day realized volatility, annualized. In a calm market (rv20 below 20%) nothing changes. When QQQ vol is 40%, the long is halved.

Why it works for a 3x ETF: daily-reset leverage trails 3× the index by roughly `3 × variance` per year (≈7% at 15% QQQ vol, ≈48% at 40%), so the cost of holding TQQQ rises with the *square* of volatility, while the trend and timing signals are least reliable in exactly those high-vol stretches (2000–02, 2008, 2022).

## Changes from v1 (October 2026 review)

| Change | Why | Evidence |
|---|---|---|
| Volatility target at 20% | Cuts exposure when leveraged-ETF decay is worst | Every target from 15% to 30% raised the Sharpe and cut the max DD, in both 2000–12 and 2013–26 |
| Weights 0.40/0.20/0.40 → 1/3 each | Equal weights are the no-opinion prior; the MR sleeve is the best diversifier | Higher Sharpe in both halves, max DD −54% → −44% before the vol target |
| Swap spread of 0.5% added to synthetic TQQQ financing | v1's synthetic TQQQ beat the real ETF by 1.6%/yr | Gap is now 0.1%/yr (41.8% synthetic vs 41.7% real since 2010) |
| Sharpe now uses returns in excess of T-bills | Holding cash earns T-bill interest, which v1 counted as skill | All Sharpe ratios below are about 0.05–0.10 lower than v1's published ones |
| `max_drawdown_halt` 0.50 → 0.45 | v2's backtest max DD is −35%; a 45% fall means something is broken | |
| SMA200 baseline added to the backtest | A timing strategy has to beat the simplest timing rule | See tables |

## Backtest (from `python -m tqqq_blend backtest`)

Setup: synthetic 3x ETFs built from QQQ total return. TQQQ pays the 0.95% expense ratio plus financing on its 2× borrowed notional at T-bills + 0.5%. SQQQ gets no collateral yield, which is conservative. Costs are 5 bps per trade. Signals use the close of day t and earn the return of day t+1. Sharpe is on excess returns.

| Mar 2000 – Oct 2026 | CAGR | Max DD | Sharpe | Trades/yr |
|---|---|---|---|---|
| **Blend v2** | **13.0%** | **−35%** | **0.61** | 50 |
| Blend v1 (0.4/0.2/0.4, no vol target) | 11.9% | −54% | 0.50 | 53 |
| Trend only | 16.1% | −49% | 0.58 | 34 |
| MR only | 9.3% | −52% | 0.40 | 24 |
| Vibha only (regime-gated) | 7.7% | −53% | 0.36 | 28 |
| TQQQ when QQQ > SMA200 | 9.7% | −95% | 0.40 | 7 |
| QQQ buy & hold | 8.1% | −83% | 0.35 | |
| TQQQ buy & hold (synthetic) | −3.4% | −100% | 0.33 | |

(The single-sleeve rows include the vol target.)

| Jan 2013 – Oct 2026 | CAGR | Max DD | Sharpe |
|---|---|---|---|
| **Blend v2** | **20.3%** | **−26%** | **0.90** |
| Blend v1 | 21.9% | −34% | 0.86 |
| Trend only | 27.5% | −47% | 0.86 |
| TQQQ when QQQ > SMA200 | 37.9% | −57% | 0.88 |
| QQQ buy & hold | 19.9% | −35% | 0.89 |
| TQQQ buy & hold | 43.0% | −81% | 0.86 |

Worst drawdowns (v2): −35% (Nov 2007 – Jul 2009, recovered Mar 2010), −34% (Mar 2000 – Apr 2003, recovered Jan 2004), −28% (2011), −26% (2018). v1's −54% from 2000 took until April 2010 to recover.

### Robustness checks

| Test | Result |
|---|---|
| Execute one day late (signal t, trade at close t+1) | No degradation (13.7% CAGR, Sharpe 0.63, vs 13.0% / 0.61). The edge doesn't depend on catching the exact close, so a missed day is not costly. |
| Costs 15 / 30 bps per trade | CAGR 11.8% / 10.1%, Sharpe 0.56 / 0.48. ~50 trades a year makes costs matter: use MOC orders. |
| Rebalance band 0.05 / 0.20 | Sharpe 0.61 / 0.62; insensitive. |
| 60 random sets of ±25% perturbations to 23 parameters | v2 beat v1 on full-period Sharpe in 60/60, on max DD in 60/60, on 2000–12 Sharpe in 58/60, on 2013–26 Sharpe in 54/60. Median v2: Sharpe 0.64, max DD −32%. |
| Are v1's parameters curve-fit? | No. Its perturbed neighborhood had a *higher* median Sharpe (0.57) than the published point (0.50). |
| Live 2-year data window vs full history | Identical net exposure on 80 of 80 random dates; the state machines warm up within the window. |
| Trend shorts on / Vibha bear cap off | Both worse, confirming v1's two in-sample choices. |

How to read these numbers:
- **The blend's job is to survive.** Over a period in which TQQQ buy-and-hold went to zero and the SMA200 rule fell 95%, v2 compounded at 13% with a 35% max drawdown.
- **In a bull decade it is "QQQ with smaller drawdowns", not a TQQQ substitute.** Since 2013 it matched QQQ's return and Sharpe with a 26% drawdown instead of 35%. Average TQQQ weight is about 34%, which is roughly 1× QQQ exposure. If you want more upside you have to accept more drawdown; the vol target can be raised to 25–30% for that.
- **The Vibha sleeve is the weakest on its own** and removing it entirely would have helped 2000–12 slightly. It is kept because it is the only sleeve that reads volume, and it helps 2013–26 and the 2022 bear.
- **Expect a losing year about every 3–4 years**: 2000 (−15%), 2001 (−13%), 2005 (−5%), 2008 (−16%), 2011 (−23%), 2016 (−7%), 2018 (−15%), 2022 (−9%).
- **None of these results compare to Mallik's claimed ~80% CAGR backtest.** His live record since July 2022 is ~22% CAGR with a 37.6% DD. This backtest shows ~27% and −17% for the same window, and a backtest beating a live record is normal, not a sign of a better system.

## What's not modeled

- Vibha's discretionary judgment: "failed retest x3", bulls/bears sentiment, how strong the move "feels", and her rotation into individual CANSLIM stocks.
- Mallik's exact thresholds and how his 7 systems combine, which are proprietary.
- Intraday data: the live runner uses the price about 20 minutes before the close and projects the day's volume. The backtest assumes the actual closing value, so live signals will occasionally differ on borderline days.
