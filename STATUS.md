# Status

## Current handoff (2026-10-22)

**Revision:** the server runs `41f8d47` (main), deployed 2026-10-22 08:11 ET through deploy/push.sh. The dashboard
(redesign #16, trade reasons #17, layout fix #18) is live behind basic auth (stack output `DashboardUrl`; login
in Secrets Manager `tqqq-trading-dashboard`) and republishes at 16:35 ET on weekdays. From the 2026-10-22 run on,
each run records `why` (explain.py): each sleeve's rule and the blend to the target.

**Verified on 41f8d47 (local, Windows, and CI on the PR heads):**
- `pwsh -NoProfile -File scripts/cross-review-selftest.ps1`: passed
- tqqq-blend: `ruff check`, `ruff format --check`, `mypy`: clean; `pytest`: 46 passed, including 13 Chromium
  dashboard tests; the explained blend reproduces the recorded net on every day of three synthetic markets
- On AWS: explain ran in the server container on live QQQ data and matched the Oct 21 decision; the dashboard
  published, answers 401 without the password and renders with it

**Running:** tqqq-blend on AWS, the IBKR paper profile (config.aws-paper.toml), $100k strategy capital, Finviz
quotes. Paper has run 2026-10-20 and 2026-10-21 (3 trades). The live profile has no login on the server yet.

**Remaining:**
- Test coverage is thin where orders are made: runner.py 27%, brokers/ibkr.py and
  brokers/schwab.py 0%. Add boundary tests for the run gates (window, kill switch, once per day,
  drawdown halt, max_order_value) and for the IBKR account-type check.
- Live: store the new IBKR live user's login once IBKR approves it, deploy, approve the push,
  run ibkr-check. Confirm whether the gateway resends a missed 2FA push.

**Next action:** after the 2026-10-22 close, confirm the 16:35 timer republished the dashboard on its own and that
the 15:30 run recorded `why` (the decision card shows rule-by-rule reasons instead of the "before reasons" note).

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
