# Status

## Current handoff (2026-10-26)

**Revision:** the server runs `933695f` (main), deployed 2026-10-26 ~21:40 ET through deploy/push.sh: buckets (#20)
and sleeves (#21). The IBKR paper account now holds five buckets, each with its own capital and share ledger
(docs/BUCKETS.md): tqqq-blend $100k (15:30), tqqq-blend mix 20% $20k (15:32), and the sleeves rotation $30k,
sectors $25k and quality $25k (15:34, one service). Fills are credited at 16:10 (trading-reconcile.timer).

**Verified on 933695f:**
- CI on both PR heads (incl. both image builds and the Windows harness); Gemini APPROVE, 0 blocking, on both.
- On AWS after the deploy: both images built, all timers enabled. Dry runs (no orders) of all five buckets:
  the $100k bucket seeded its ledger with the account's 233 TQQQ (no change: inside its band); the mix bucket
  would buy 48 TQQQ; the sleeves (forced decision) would buy DBC/EFA/QQQ/SPY, XLE/XLK/XLV and 19 stocks, on
  Finviz quotes; every run passed the cross-bucket account check.

**Not yet verified:** the 16:35 dashboard publish with the mix-portfolio view and the new buckets (first build
2026-10-27); a live reconcile of real MOC fills (first sleeves orders 2026-10-30). The sleeves dry runs left a
dry-run "Latest decision" on their dashboard cards until then.

**Remaining:**
- After the 2026-10-27 close: check the mix bucket's first real TQQQ order filled and the 16:10 reconcile
  credited it (`reconcile.jsonl`, `fills.csv`), and the dashboard shows the portfolio and five buckets.
- 2026-10-30 (month end): the sleeves' first rebalance. Check orders, fills and reconcile that evening.
- Schwab: no fill reports, so a Schwab profile refuses to trade (none is deployed).
- Live: store the new IBKR live user's login once IBKR approves it, deploy, approve the push, run ibkr-check.

**Next action:** after the 2026-10-27 close, check the reconcile and the dashboard (above).

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
