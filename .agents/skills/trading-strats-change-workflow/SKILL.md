---
name: trading-strats-change-workflow
description: Implement, verify, review, and hand off code, config, deployment or documentation changes in the Trading_Strats repository (automated trading strategies such as tqqq-blend). Use for any Trading_Strats development work that should follow its branch, testing, status, cross-model review, guarded-merge and manual-deploy workflow; do not use for market research or ordinary questions about a strategy.
---

# Trading_Strats change workflow

Read `AGENTS.md`, `docs/PLAN.md`, and the current handoff at the top of `STATUS.md` before changing behavior. Treat the user's current instruction as authoritative within the boundaries in `AGENTS.md`.

Use a focused `feat/`, `fix/`, or `docs/` branch. Inspect the worktree before editing and preserve unrelated changes. When multiple agents work concurrently, declare file ownership before editing shared files.

These strategies place real orders. Never weaken or bypass the live switches (`--live` plus `live_trading_enabled`), the account-type check, the near-close window, the once-per-day guard, the kill switch, the drawdown halt or `max_order_value`. Never place an order from an agent session; dry runs and `ibkr-check` are fine. Changes to capital, limits or live switches need the owner's explicit request. Strategy logic changes need a before/after backtest; critical-path changes need tests that exercise their boundaries.

Run checks that cover the changed behavior, then the repository-required lint, types, and tests from the strategy folder (`uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest`). Do not claim a check passed unless it ran on the current revision. For harness changes, run:

```powershell
pwsh -NoProfile -File scripts/cross-review-selftest.ps1
uv run --no-project --with pyyaml python scripts/validate_skills.py
```

Update the current handoff in `STATUS.md` with the exact revision or worktree state tested, commands run, results, limitations, and next action. Keep historical narrative below the handoff.

AI-authored commits must carry the appropriate `Co-Authored-By` trailer. When delivery is in scope, open a PR, wait for CI on the final head, run `scripts/cross-review.ps1 -Number <PR>`, address or rebut every blocking finding, and use `scripts/merge-pr.ps1 -Number <PR>` so the reviewed SHA and successful CI are enforced. Deploying is a separate, manual step (`<strategy>/deploy/push.sh`, merged revisions only, typed confirmation); do it only when the owner asks.
