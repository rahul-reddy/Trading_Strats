# tqqq-blend

An automated TQQQ/SQQQ strategy that blends Mallik's systematic trend-following and mean-reversion approach with Vibha Jha's market-timing rules. It makes one decision a day near the US close and executes through **Interactive Brokers** or **Schwab**, or through a local paper account.

The rules, sources, and backtest results are in [STRATEGY.md](STRATEGY.md). For a plain-language walkthrough and the operating routine, start with [WORKFLOW.md](WORKFLOW.md).

> TQQQ lost about 80% in 2022 and would have lost more than 99% in 2000–02. This strategy's backtest still shows drawdowns of 26–35% (v2). Only allocate money you can watch fall by half without turning the system off. Nothing here is financial advice.

## Layout

```
tqqq_blend/
  strategy.py     the three sleeves and the blend (pure functions on a QQQ DataFrame)
  indicators.py   SMA/EMA/RSI/Bollinger/realized vol
  backtest.py     synthetic 3x ETFs since 1999, sleeve comparison, synthetic-vs-real check
  runner.py       daily process: gate -> signal -> risk -> plan -> execute -> log
  brokers/        paper.py, ibkr.py (ib_async), schwab.py (schwab-py)
  config.py       all parameters with defaults; config.toml overrides them
config.toml                 strategy and risk settings
config.ibkr-{paper,live}.toml   IBKR profiles: extend config.toml, own state_dir, account-type check
config.aws-{paper,live}.toml    the same profiles pointed at the gateways on the AWS server
scripts/register_task.ps1   Windows Task Scheduler setup
deploy/                     AWS server: Docker, IB Gateway, systemd timers (see deploy/README.md)
tests/                      pytest
state/                      runtime files: cache, state.json, trades.csv, runs.jsonl, STOP
```

## Setup

```powershell
cd C:\dev_projects\Trading_Strats\tqqq-blend
uv sync --locked                              # installs exactly what uv.lock pins, into .venv
uv run pytest
uv run python -m tqqq_blend backtest          # full history; writes CSVs to state/backtest
uv run python -m tqqq_blend signal            # last 15 days of sleeve diagnostics
```

## Going live in stages

Don't skip a stage. Each one catches a different class of mistake.

| Stage | Duration | Command / config | What you're checking |
|---|---|---|---|
| 1. Backtest | now | `backtest`, `signal` | You understand and accept the drawdowns |
| 2. Local paper | 2–4 weeks | `register_task.ps1` (broker=paper) | The schedule fires, signals match `signal`, logs look right |
| 3. Broker paper | 4+ weeks | IBKR paper account (port 7497); `register_task.ps1 -Broker ibkr -Live` with `live_trading_enabled = true` | Real order routing, MOC fills, position reconciliation |
| 4. Live, small | 3+ months | Live port; `capital` at 10–25% of the intended amount | Slippage, behavior during a real drawdown, your own nerve |
| 5. Scale | after that | Raise `capital` | |

Schwab has no paper trading API, so test against IBKR paper or the local paper broker before pointing it at Schwab.

## Broker setup

### Interactive Brokers
1. Install **IB Gateway** (lighter) or TWS and log into the **paper** account first.
2. Go to *Configure → API → Settings*:
   - enable socket clients
   - socket port 7497 (TWS paper) or 4002 (Gateway paper)
   - add `127.0.0.1` to trusted IPs
   - **uncheck Read-Only API**
3. Gateway logs out daily. Use its auto-restart setting, or [IBC](https://github.com/IbcAlpha/IBC) for unattended logins.
4. Use a profile instead of editing `config.toml`: `config.ibkr-paper.toml` (port 7497) and `config.ibkr-live.toml` (port 7496). Each one keeps its own ledger and logs in `state/ibkr-paper` or `state/ibkr-live`. Each sets `ibkr_account_type`, and a run refuses to start if the login's account is the other type (paper IDs start with `D`).
5. Test the connection read-only: `python -m tqqq_blend --config config.ibkr-paper.toml ibkr-check`. It prints the account, balances, positions and quotes, and never places orders.
6. Orders go in as **MOC** (market-on-close) when submitted before 15:50 ET, otherwise as market orders.

### Schwab
1. Create an app at developer.schwab.com with the *Accounts and Trading Production* product. Callback URL: `https://127.0.0.1:8182`. Approval can take a few days.
2. `setx SCHWAB_API_KEY "..."` and `setx SCHWAB_APP_SECRET "..."`, then open a new terminal.
3. `python -m tqqq_blend schwab-login`. It opens a browser and writes `state/schwab_token.json`.
4. **Schwab refresh tokens expire every 7 days.** Re-run the login weekly, or the run fails and logs an error. That failure is safe: no orders are placed.
5. Set `[broker] name = "schwab"`.

## Daily process (what `run` does)

1. **Gates.** Each one stops the run:
   - the market must be open today
   - 5–30 minutes must remain to the close
   - `state/STOP` must not exist
   - `state.json` must not carry a `halted` flag
   - the run hasn't already completed today
2. **Signal.** Pull two years of QQQ, replace today's close with the live QQQ price, project today's volume to a full day, and compute the sleeves and target weights.
3. **Risk.**
   - halt if the data is stale
   - halt if QQQ moved more than 8% today
   - halt if strategy equity is 50% or more below its peak (needs a manual reset)
   - reject any order larger than `max_order_value`
4. **Ledger.** Read this bucket's own shares and cash (`ledger.py`), and stop unless the account holds exactly what every bucket's ledger on it adds up to (`docs/BUCKETS.md`), or if an earlier day's order was never matched to fills.
5. **Plan.** Diff target shares against the bucket's shares. Skip changes inside the 10% band. Sells go first.
6. **Execute.** Orders are only sent when `--live` is passed **and** `live_trading_enabled = true`. Otherwise the run logs DRY-RUN. The paper broker always fills. Each order carries the bucket's tag (IBKR `orderRef`).
7. **Log** to `state/runs.jsonl` (every run, with the sleeve values), `state/trades.csv`, and `tqqq_blend.log`.

After the close, `python -m tqqq_blend reconcile` credits the day's fills to the bucket (`state/fills.csv`, `state/reconcile.jsonl`). On AWS it runs at 16:10 ET.

**Kill switch:** `New-Item state\STOP` stops trading on the next run. Delete the file to resume. Existing positions are not closed automatically.

**Strategy capital (buckets):** each profile is a bucket with its own ledger of cash and shares, seeded with `risk.capital`, so several buckets (other strategies, or another copy of this one such as `config.aws-paper-mix.toml`) can share an account and even trade the same symbols. A bucket owns only the shares its own fills bought. It never sizes above the account's net liquidation value. See [docs/BUCKETS.md](../docs/BUCKETS.md).

## Scheduling (Windows)

```powershell
.\scripts\register_task.ps1                      # paper broker
.\scripts\register_task.ps1 -Broker ibkr -Live   # real orders (also needs live_trading_enabled = true)
```

The task fires every 5 minutes from 15:30 to 15:50 ET. The first successful run of the day acts, and later runs skip. The PC has to be on and awake: the task is set to wake it, but a laptop with its lid closed may not run. For unattended live trading, use the AWS server in [deploy/](deploy/README.md). On half-days (13:00 close) the 15:30 window misses the close, so no trade happens; that's acceptable for this strategy.

## Dashboard

`python -m tqqq_blend dashboard` writes `state/dashboard/index.html`: each profile's latest decision,
performance against QQQ and TQQQ buy-and-hold, drawdown, net exposure, run health and trades, read from the
state files the runs already write. On AWS it is rebuilt after every close and published behind a password
(see [deploy/README.md](deploy/README.md#dashboard)).

## Accounts and tax

- Both traders run this in **IRAs**. About 50 trades a year in a taxable account turns most gains into short-term gains.
- In a cash account or IRA, the sell and buy on the same day use the same-day sale proceeds. That is fine under T+1 settlement as long as you don't sell the new position before the sale settles.
- You may need to accept the broker's leveraged-ETF risk disclosure before TQQQ/SQQQ orders are allowed.
