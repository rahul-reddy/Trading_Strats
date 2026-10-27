# Plan

## Direction

Trading_Strats holds automated trading strategies, one self-contained folder per strategy. Each one
makes its decisions from data and rules, places orders through a broker API, and runs unattended
on a schedule. The repository's job is to keep that safe: every strategy is backtested, paper
traded, then live traded small before it is scaled, and every change is tested and reviewed by a
second AI model before it merges.

## Strategies

| Strategy | Stage | Notes |
|---|---|---|
| tqqq-blend | 3. Broker paper (IBKR paper, $100k strategy capital) since 2026-10-20 | Live account: dry runs once the new IBKR user is approved |
| sleeves (ETF rotation, sector momentum, quality-value) | 3. Broker paper from 2026-10-30 (first month-end) | Each its own bucket: $30k / $25k / $25k |
| Mix portfolio ($100k paper, 2026-10-26) | 3. Broker paper | 20% tqqq-blend (`config.aws-paper-mix.toml`) + the three sleeves buckets; combined on the dashboard |

Stages are defined in tqqq-blend/README.md ("Going live in stages"): backtest, local paper, broker
paper (4+ weeks), live small (3+ months), scale.

## Decisions

- 2026-10-19: One repository, one folder per strategy, each with its own README, STRATEGY.md,
  WORKFLOW.md, config, tests and lockfile.
- 2026-10-20: tqqq-blend runs on AWS (us-east-1, t3.medium). EventBridge starts the server at
  15:00 ET and stops it at 16:45 ET on weekdays; the owner approves one IB Key push a day for live
  rather than keep the server on all the time (~$5/month instead of ~$36).
- 2026-10-20: Separate IBKR usernames for paper, for live-on-server, and for the owner's own use,
  because IBKR allows one session per username.
- 2026-10-20: Paper strategy capital $100k; live starts at $10k with orders disabled.
- 2026-10-20: Quotes come from Finviz Elite (real time). IBKR quotes are the recorded fallback.
- 2026-10-20: Adopted the TenFold workflow: short branches, squash PRs, CI on the exact head,
  cross-model review, merges only through scripts/merge-pr.ps1. Harness copied from TenFold at
  9d6b56e.
- 2026-10-20: Deployment is manual: deploy/push.sh ships a merged commit (git archive) after a
  typed confirmation. No container registry: the server builds the image from that commit's
  uv.lock, so there is nothing to publish on merge.
- 2026-10-20: The Gemini reviewer runs through the Antigravity CLI on the owner's Google (Pro)
  login, replacing the Gemini CLI (API key: 429 on every review). Antigravity offers Gemini 3.8
  Flash only. Gemini reviews every tier, critical included, at high effort (medium on low-tier
  diffs; owner, same day).
- 2026-10-26: Buckets (owner: "test individual strategies in their own buckets even though there is
  an overlap", shown on the dashboard). Several strategies share the IBKR paper account, each with its
  own capital and a ledger of the shares its own fills bought (IBKR orderRef), reconciled after the
  close and checked against the whole account before every trade (docs/BUCKETS.md).
- 2026-10-26: A second $100k paper portfolio, split 20% tqqq-blend / 30% ETF rotation / 25% sector
  momentum / 25% quality-value (owner's split). Momentum uses the SPDR sector ETFs rather than 25 single
  stocks: similar backtest (2000-2026: 8.2% CAGR, -22% max drawdown vs 7.3%, -19%), testable exactly
  as traded, and the sector cap the owner asked for is built in.
