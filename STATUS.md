# Status

## Current handoff (2026-10-26)

**Merged:** buckets (#20, 04e0744): tqqq-blend buckets own only the shares their own fills bought (IBKR orderRef,
reconciled at 16:10 ET, the whole account checked against every bucket's ledger before a trade); the $20k mix
bucket (`config.aws-paper-mix.toml`); reconcile alerts on the dashboard. Not yet deployed.

**In review:** sleeves (branch `feat/sleeves`): ETF rotation ($30k), SPDR sector momentum ($25k) and quality-value
($25k) buckets for the owner's second $100k paper portfolio (20/30/25/25), their deploy (bundled by push.sh under
`strategies/sleeves`, `sleeves-paper.timer` at 15:34-15:49 ET), and the dashboard's mix-portfolio view and
holdings buckets. rules/schedule/ledger.py are now critical paths for review tiering.

**Verified on feat/sleeves (local, Windows):** tqqq-blend `ruff`, `mypy` clean, `pytest` all passed with Chromium
(incl. portfolio and holdings browser tests, phone width, dark mode); sleeves `ruff`, `mypy` clean, `pytest` 63
passed; harness self-test passed. Live smoke: `signal` for all three sleeves on real data and Finviz (rotation
DBC/SPY/QQQ/EFA; sectors XLE/XLK/XLV; quality 19 stocks); a local paper rebalance credited its fills to the ledger.
Backtests with the live rules: rotation 9.5% CAGR, -18.4% max DD since 2008; sectors 7.9%, -24.8% since 2000; the
20/30/25/25 mix 11.1%, -17.9% (SPY 11.8%, -46.3%), quality from its CRSP proxy (sleeves/STRATEGY.md).

**Running:** the server still runs `41f8d47`: tqqq-blend paper, $100k.

**Remaining:**
- sleeves: CI, cross-review, merge; then deploy both merges with deploy/push.sh, and check the first run seeds the
  $100k bucket's ledger, the mix bucket and the sleeves buckets value themselves, and the 16:10 reconcile runs.
- First sleeves rebalance: Friday 2026-10-30 (month end). Check orders, fills and the dashboard that evening.
- Schwab: no fill reports, so a Schwab profile refuses to trade (none is deployed).
- Live: store the new IBKR live user's login once IBKR approves it, deploy, approve the push, run ibkr-check.

**Next action:** open the sleeves PR, run CI and scripts/cross-review.ps1, merge, deploy.

## History

- 2026-10-26: Buckets (#20). The owner asked for a second $100k paper portfolio, 20/30/25/25 across tqqq-blend,
  ETF rotation, sector momentum and quality-value, each slice tested as its own bucket and shown on the dashboard.
  Research behind the sleeves: 19-year ETF backtests and CRSP factor data (sleeves/STRATEGY.md). Gemini's review
  of #20 found three real issues (reconcile exiting 0 on failure, Schwab able to send orders before refusing,
  stale sibling orders widening the account check), all fixed before merge.

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
