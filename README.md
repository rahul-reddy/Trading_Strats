# Trading_Strats

Systematic trading strategies, one folder per strategy. Each folder is self-contained, with its own code, config, tests, and docs, so strategies can evolve independently.

| Strategy | What it trades | Status | Start here |
|---|---|---|---|
| [tqqq-blend](tqqq-blend/) | TQQQ / SQQQ, one decision a day near the close. Blends Mallik's trend and mean-reversion systems with Vibha Jha's market timing. | v2, paper-trading stage | [WORKFLOW.md](tqqq-blend/WORKFLOW.md) |

## Adding a strategy

1. Create a new folder `<strategy-name>/` with at least:
   - `README.md`: setup and how to run
   - `STRATEGY.md`: the exact rules, sources, and backtest results
   - `WORKFLOW.md`: plain-language walkthrough and operating routine
   - `config.toml`, `requirements.txt`, `tests/`
2. Keep runtime files (data cache, backtest output, broker tokens, trade logs) in `<strategy-name>/state/`, which is git-ignored.
3. Never commit API keys or broker tokens. Use environment variables.
4. Add a row to the table above.

## Conventions

- Backtests report CAGR, max drawdown, and Sharpe on excess returns, over the full history *and* a recent sub-period.
- A parameter change is kept only if it helps in both halves of the history and its neighboring values help too.
- Every strategy goes backtest → local paper → broker paper → small live → scale, and doesn't skip a stage.

Nothing here is financial advice.
