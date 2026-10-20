# How to understand and operate TQQQ Blend

This is a walkthrough, not the spec. The exact rules are in [STRATEGY.md](STRATEGY.md); the setup steps are in [README.md](README.md). Read this first.

## 1. The idea in one paragraph

TQQQ is 3× the daily move of the Nasdaq-100. Held through a crash it goes to near zero (−100% in 2000–02, −79% in 2022). The money is made by holding it in calm uptrends and getting out, or small, when the market is falling and volatile. The strategy asks three independent "should I be in?" questions every afternoon, averages the answers, shrinks the result when the market is volatile, and holds that fraction of capital in TQQQ. The rest sits in T-bills.

## 2. The daily decision, step by step

```
QQQ daily bar (today's close ≈ price at 3:40 pm ET, volume projected to full day)
        │
        ├──► Trend sleeve   (Mallik)  "Is QQQ in a long-term uptrend, and how stretched is it?"   0 … 1
        ├──► MR sleeve      (Mallik)  "Is this a short-term oversold dip worth buying?"           -1 … 1
        └──► Vibha sleeve   (Jha)     "Is the market under accumulation or distribution?"         0 … 1
                         │
                 average of the three  ──►  net
                         │
         volatility target: net × min(1, 20% / QQQ 20-day vol)
                         │
         net > 0 → that fraction in TQQQ     net < 0 → half of |net| in SQQQ
                         │
         trade only if the weight moved by more than 0.10 → MOC order before 3:50 pm
```

### Sleeve 1: Trend (Mallik's momentum system)
- **Question:** is QQQ above its 250-day average (with a 1% buffer so it doesn't flip every day)?
- **If yes:** full size, stepped down as QQQ gets stretched far above the average (15% above → 0.75, 25% above → 0.50), halved if it has dipped below the 20-day, and back to full if it's breaking out above the upper Bollinger band.
- **If no:** flat. (Momentum shorts lost money in testing and are off.)
- **Character:** slow. About 34 trades a year, mostly small resizes. This sleeve makes most of the money in bull markets.

### Sleeve 2: Mean reversion (Mallik's MR systems)
- **Question:** has QQQ fallen hard for 1–3 days (RSI(2) below 10) while still in an uptrend?
- **If yes:** buy, and sell when RSI(2) gets back above 70 or after 7 days.
- **In a downtrend:** a half-size bounce trade if extremely oversold, and a short SQQQ rally-fade when RSI(2) goes above 90.
- **Character:** in the market only 15% of the time. It buys exactly when the other two sleeves are cutting, which is why it makes the blend smoother.

### Sleeve 3: Vibha Jha's market timing (IBD-style)
- **Buys:** a *follow-through day* (day 4+ of a rally attempt, up ≥1.25% on higher volume) → 100%; three days of higher highs and higher lows → 25–75% pilot; reclaiming the 50-day → 50%.
- **Sells:** counts warning flags each day (distribution days, three down days, losing the 10-day on volume, a new high on weak volume). As flags stack up it trims 25% at a time; 5+ distribution days caps it at 50%; a close below the 21-day EMA is a full exit.
- **Blend rule:** below the 250-day average the sleeve is capped at 50%, because follow-through days failed repeatedly in 2000–02 and 2008.
- **Character:** the fastest sleeve and the only one that reads volume. Weakest alone, but it's out early in tops like late 2021.

### The volatility target
Because TQQQ resets its leverage daily, it trails 3× QQQ's return by about 3 × variance per year. At 15% QQQ vol that's ~7%/yr; at 40% vol it's ~48%/yr. So when QQQ's 20-day vol is above 20%, the TQQQ position is scaled down in proportion (40% vol → half size). This change cut the backtest's worst drawdown from −54% to −35%.

## 3. Reading today's signal

```powershell
python -m tqqq_blend signal --days 10
```

| Column | What it tells you |
|---|---|
| `ext` | How far QQQ is above its 250-day average. Negative means bear regime. |
| `rsi_mr` | 2-day RSI. Below 10 in an uptrend means the MR sleeve is buying. |
| `dist_days`, `sell_flags` | Vibha's warning count. Rising numbers mean the sleeve is trimming. |
| `trend`, `mr`, `vibha` | Each sleeve's vote, 0 to 1 (MR can be −1). |
| `vibha_event` | What Vibha's sleeve did that day: `FTD`, `HHHL`, `reclaim50`, `trim(…)`, `distcap(…)`, `exit<21ema`. |
| `vol_scale` | The volatility multiplier. 1.0 means calm. |
| `net`, `w_long`, `w_short` | The final target: fraction of strategy capital in TQQQ / SQQQ. |

**Worked example (Oct 16, 2026 close):** QQQ is 7% above its 250-day, so the trend sleeve is long but halved (below its 20-day) at 0.50. RSI(2) is 0.6, so the MR sleeve bought the dip at 1.0. Vibha exited on Oct 14 below the 21-day EMA, with 6 distribution days, so 0. Net = (0.50 + 1.0 + 0) / 3 = 0.50. QQQ vol is 18.6%, under 20%, so there's no scaling. Target: 50% TQQQ.

## 4. Operating it

| When | What to do | Command / file |
|---|---|---|
| Once, before anything | Read STRATEGY.md and accept the drawdowns: −35% in backtest, plan for −45% live | |
| Once | Run the tests and the backtest, and check they match STRATEGY.md | `python -m pytest -q`, `python -m tqqq_blend backtest` |
| Daily, 3:30–3:50 pm ET | The scheduled task runs; the first successful run acts | `scripts/register_task.ps1` |
| Daily, after close | Glance at the log: status `executed` / `no change`, not `error` | `state/runs.jsonl`, `tqqq_blend.log` |
| Weekly | Compare live weights to `signal` output; re-login Schwab (tokens expire after 7 days) | `python -m tqqq_blend signal`, `schwab-login` |
| Monthly | Compare realized fills against the reference price to check slippage | `state/trades.csv` |
| Quarterly | Re-run the backtest with fresh data (`--refresh`) and check nothing drifted | |
| Emergency | Create the kill-switch file; the next run does nothing | `New-Item state\STOP` |
| After a drawdown halt | Investigate before clearing `halted` in `state/state.json` | |

### When to override (and when not to)
- **Don't** turn it off during a drawdown that is inside the backtest's range (down to about −35%). That is the system working; 2011 and 2018 both looked like this and recovered within a year.
- **Do** stop and investigate if the live weights disagree with `signal` output for more than a day, if fills are far from the reference price, or if the drawdown passes −45% (the breaker does this automatically).
- Missing a day is cheap: executing one day late made no difference in the backtest.

## 5. Changing parameters safely

1. Change one thing in `config.toml`.
2. Run `python -m tqqq_blend backtest` and compare against **both** halves: `--start 2000-03-01` and `--start 2013-01-01`.
3. Keep the change only if it helps in both halves *and* the neighboring values also help. A setting that only works at one exact value is noise.
4. Run the tests, then paper-trade the change for a few weeks before it touches real money.

## 6. What can go wrong that the backtest doesn't show

- **A crash bigger than 2000 or 2008.** A one-day QQQ fall of 33%+ wipes out TQQQ, and the vol target can't react inside one day. The `max_signal_move` halt protects against bad data, not against this.
- **Intraday differences.** Live signals use the price about 20 minutes before the close and projected volume. Borderline days (e.g. a distribution day by 0.05%) can come out differently.
- **Costs and taxes.** ~50 trades a year. In a taxable account nearly all gains are short-term. Use an IRA.
- **The source traders' rules are inferred, not copied.** Neither Mallik nor Vibha publishes exact thresholds; this is a translation of their public descriptions.
