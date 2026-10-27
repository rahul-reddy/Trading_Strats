# Status

## Current handoff (2026-10-26)

**In progress:** buckets (branch `feat/buckets`, PR 1 of 2) for the owner's second $100k paper portfolio, split
20% tqqq-blend / 30% ETF rotation / 25% SPDR sector momentum / 25% quality-value, each slice its own bucket in the
shared IBKR paper account and on the dashboard (docs/BUCKETS.md). PR 1: tqqq-blend buckets own only the shares
their own fills bought (orderRef-tagged, reconciled at 16:10 ET, checked against the whole account before each
trade); the $20k mix bucket (`config.aws-paper-mix.toml`); reconcile alerts on the dashboard. PR 2: `sleeves/`
with the other three buckets.

**Verified on the feat/buckets working tree (local, Windows):** tqqq-blend `ruff check`, `ruff format --check`,
`mypy` clean; `pytest` 88 passed with Chromium (runner.py 27% -> 96%, ledger.py 99%, brokers/ibkr.py 0% -> 51%);
a local paper smoke run of two buckets: the second refused on a mismatched account, then traded in its own group.

**Running:** the server still runs `41f8d47`: tqqq-blend on the IBKR paper profile, $100k, Finviz quotes. On the
first run after this deploys, that bucket seeds its share ledger from the account's TQQQ/SQQQ once.

**Remaining:**
- PR 1: CI, cross-review, merge, deploy; check the first 15:30 run seeded the ledger and the 16:10 reconcile ran.
- PR 2: sleeves/ (ETF rotation, sector momentum, quality-value), its backtest, deploy, and the mix view on the
  dashboard. First rebalance: Friday 2026-10-30 (month end).
- brokers/schwab.py has no fill reports: a Schwab profile now refuses to trade (none is deployed).
- Live: store the new IBKR live user's login once IBKR approves it, deploy, approve the push, run ibkr-check.

**Next action:** open PR 1, run CI and scripts/cross-review.ps1.

## History

- 2026-10-22: Trade reasons (#17): explain.py turns a day's signal into each sleeve's rule plus the blend
  arithmetic; the runner records it (fenced: a failure can't change a run); the dashboard shows what it did
  (held vs target against the rebalance band), why, and how it adds up, and a Why column for trades. Strategy
  logic unchanged. Dashboard redesign (#16): overview row, KPI row with drawdown meter, range buttons, plain-
  language decision, activity tabs, stale-page banner, exposure bars inside the plot. Order layout fix (#18).
  Deployed 41f8d47.

- 2026-10-21: Deployed. The deploy user's permissions became a managed policy (inline policies are capped at
  2,048 characters; #13). Stack update added the Amplify app, branch and secret (no replacements). The first
  push.sh run failed: archiving the tqqq-blend subtree skipped the root .gitattributes, so autocrlf shipped
  CRLF scripts (`set: pipefail: invalid option`). Rolled the server back over SSM, fixed and tested (#14),
  redeployed 19de244. First publish succeeded; verified 401 without and wrong password, 200 with it,
  no-store/noindex/DENY headers, no account-ID-shaped strings on the page. Server stopped afterwards.

- 2026-10-21: Dashboard (branch `feat/dashboard`): `python -m tqqq_blend dashboard` builds one static page
  from the state files (decision, performance vs QQQ/TQQQ buy-and-hold, drawdown vs halt, exposure, run
  health, trades). On AWS, `trading-dashboard.timer` (16:35 ET) publishes it to an Amplify app behind a
  generated basic-auth password; the publish refuses if auth is off and checks for 401 afterwards.
  Verified locally: ruff, mypy, 29 tests incl. 4 Chromium browser tests, cfn-lint, compose config, and a
  build in the real image with networking off. Not yet done (owner): update the deploy-user policy,
  deploy the stack, push.sh. See tqqq-blend/deploy/README.md#dashboard.

- 2026-10-21: Repository made public (it will switch between public and private for Actions
  minutes). Scan of all history, PR comments and the last 40 CI logs: no secrets or account
  numbers. Fixed the merge gate trusting verdict markers from any commenter (now owner only, with
  forgery self-tests), made the gitleaks scan blocking, added a ruleset on main and turned on secret
  scanning and push protection. Author email in 15 early commits is a personal address
  (can't be removed without rewriting history); this clone now commits with the noreply address.

- 2026-10-20: Gemini reviewer moved to the Antigravity CLI (agy, Google login): pinned
  gemini-3.8-flash-high, plan mode + sandbox, prompt on stdin. Verified end to end: approves a
  clean change, blocks a planted CAGR sign bug. Owner then allowed it on critical diffs at high effort, in all three repos.

- 2026-10-20: Found the gitleaks secret scan had never run on a PR (missing pull-requests: read and
  a shallow checkout; hidden by continue-on-error). Fixed with the Dependabot action bumps (checkout, setup-uv,
  upload-artifact, gitleaks-action) in one PR. tzdata 2026.5 merged from Dependabot (#6).

- 2026-10-20: AWS deployment (scheduled EC2, IB Gateway + IBC in Docker, SSM secrets), Finviz
  quotes, paper capital $100k, pandas 3 volume dtype fix.
- 2026-10-19: Repository created with tqqq-blend v2.
