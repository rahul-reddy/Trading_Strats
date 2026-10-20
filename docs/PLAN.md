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
