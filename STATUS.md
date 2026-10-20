# Status

## Current handoff (2026-10-20)

**Revision:** branch `feat/workflow-harness` (adopting the review and merge workflow). The server
runs the code of `f2da519` (deployed from the working tree, before push.sh required merged commits).

**Verified on this branch (local, Windows):**
- `pwsh -NoProfile -File scripts/cross-review-selftest.ps1`: passed
- `uv run --no-project --with pyyaml python scripts/validate_skills.py`: passed
- tqqq-blend: `uv sync --locked`, `ruff check`, `ruff format --check`, `mypy`: clean;
  `pytest --cov`: 14 passed, 49% line coverage (incl. deploy bundle tests: push.sh ships the commit, never the working tree)
- Strategy output unchanged by the lint/format pass: compute_signals on QQQ since 1999 hashes the
  same before and after (6,945 rows)
- `docker build -f deploy/Dockerfile .` from uv.lock: builds, `signal` runs

**Running:** tqqq-blend on AWS, the IBKR paper profile (config.aws-paper.toml), $100k strategy capital, Finviz
quotes. First paper run is 2026-10-20 15:30 ET.

**Remaining:**
- Test coverage is thin where orders are made: runner.py 27%, brokers/ibkr.py and
  brokers/schwab.py 0%. Add boundary tests for the run gates (window, kill switch, once per day,
  drawdown halt, max_order_value) and for the IBKR account-type check.
- Live: store the new IBKR live user's login once IBKR approves it, deploy, approve the push,
  run ibkr-check. Confirm whether the gateway resends a missed 2FA push.
- Gemini reviewer: needs `~/.gemini/settings.json` with gemini-api-key auth (and possibly Cloud
  Billing for the pinned model). Grok: needs XAI_API_KEY in this repo's .env.

**Next action:** check the 15:30 paper run (`/opt/trading/state/ibkr-paper/runs.jsonl`, trades.csv).

## History

- 2026-10-20: Found the gitleaks secret scan had never run on a PR (missing pull-requests: read;
  hidden by continue-on-error). Fixed with the Dependabot action bumps (checkout, setup-uv,
  upload-artifact, gitleaks-action) in one PR. tzdata 2026.5 merged from Dependabot (#6).

- 2026-10-20: AWS deployment (scheduled EC2, IB Gateway + IBC in Docker, SSM secrets), Finviz
  quotes, paper capital $100k, pandas 3 volume dtype fix.
- 2026-10-19: Repository created with tqqq-blend v2.
