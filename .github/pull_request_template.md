## Change
Describe the problem and resulting behavior.

## Verification
- [ ] Relevant tests, lint and typing pass (from the strategy folder).
- [ ] Order safety reviewed: live switches, account-type check, run gates and limits are not weakened; no orders placed from an agent session; no secrets.
- [ ] Strategy logic changed: before/after backtest attached. Capital, limits or live switches changed: owner's request quoted.
- [ ] STATUS.md and affected documentation updated.
- [ ] `scripts/cross-review.ps1 -Number <this PR>` run, with zero blocking findings on
      the current head (see AGENTS.md "Cross-model code review"). N/A only if every
      commit here is human-authored with no AI Co-Authored-By trailer.

## Limitations
State unimplemented behavior and external prerequisites explicitly. Say whether this needs a deploy (deploy/push.sh).
