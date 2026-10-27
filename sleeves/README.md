# sleeves

Three monthly, long-only strategies, each its own bucket in a shared broker account: **ETF rotation**,
**SPDR sector momentum** and **quality-value stocks**. With a 20% tqqq-blend bucket they make the owner's
$100k mix paper portfolio (20/30/25/25). The rules and backtests are in [STRATEGY.md](STRATEGY.md); how
the buckets share one account is in [docs/BUCKETS.md](../docs/BUCKETS.md).

```
sleeves/
  sleeves/rules.py       the three sleeves' rules (pure functions; the backtest uses them too)
  sleeves/schedule.py    month-end checks, catching up a missed one, quality's yearly rebalance
  sleeves/runner.py      the near-close run and the post-close reconcile
  sleeves/data.py        yfinance history, Finviz quotes and the S&P 500 screen
  sleeves/ledger.py      bucket ledgers (a copy of tqqq-blend's)
  sleeves/brokers/       IBKR and the local paper broker
  config.toml            local defaults (local paper broker)
  config.aws-paper*.toml the three paper buckets on the AWS server
  deploy/Dockerfile      the image; the server, gateways and timers are in tqqq-blend/deploy
```

## Run it

From this folder:

```
uv sync --locked
uv run python -m sleeves --config config.aws-paper-rotation.toml signal    # what the rules hold today
uv run python -m sleeves backtest --kind rotation --start 2007-01-01
uv run python -m sleeves backtest --kind sectors
uv run python -m sleeves run --force --check-now                            # local paper run, deciding today
```

`signal` and `quality` need the Finviz Elite token in `FINVIZ_TOKEN`. `run` without `--live` is a dry run
for an IBKR profile; the local paper broker always fills (simulated).

Checks: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest --cov=sleeves`.

## On the server

Each bucket is a Compose service in `tqqq-blend/deploy/docker-compose.yml` (`sleeves-rotation`,
`sleeves-sectors`, `sleeves-quality`) using this folder's image. `sleeves-paper.timer` runs the three one
after another at 15:34, 15:39, 15:44 and 15:49 ET; the 16:10 reconcile credits their fills. Every day a run
values the bucket (the dashboard's daily point); it trades only at a month-end.

| Bucket | Profile | Capital | Client ID | Drawdown halt |
|---|---|---|---|---|
| ETF rotation | `config.aws-paper-rotation.toml` | $30,000 | 21 | 30% |
| Sector momentum | `config.aws-paper-sectors.toml` | $25,000 | 22 | 40% |
| Quality-value | `config.aws-paper-quality.toml` | $25,000 | 23 | 50% |

Kill switch for one bucket: `touch /opt/trading/state/sleeves-<kind>-paper/STOP`.
