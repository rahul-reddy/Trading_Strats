# Agent harness

Trading_Strats uses a small repository-native harness for coding agents. It does not run an
LLM inside any strategy and does not grant an agent authority over orders.

## Responsibilities

| Component | Responsibility |
| --- | --- |
| `AGENTS.md` | Non-negotiable repository, safety, authorship and review contract. |
| `.agents/skills/trading-strats-change-workflow/` | Discoverable workflow guidance for implementing and handing off a Trading_Strats change. |
| `docs/PLAN.md` | Strategy direction and decisions. |
| `CLAUDE.md` | Imports `AGENTS.md` so Claude Code loads the same contract. |
| `STATUS.md` | Current handoff followed by historical implementation evidence. |
| `scripts/cross-review.ps1` | Runs an eligible independent reviewer against an exact commit. |
| `scripts/lib/cross-review.ps1` | Shared deterministic classification, marker parsing, merge-gate and process helpers. |
| `scripts/merge-pr.ps1` | Requires successful CI and a valid same-head review before squash merge. |
| `scripts/cross-review-selftest.ps1` | Dependency-free regression checks for the review gate and bounded process runner. |
| `scripts/validate_skills.py` | Validates repository skill discovery metadata in CI. |

The skill explains when and why to perform the workflow. The scripts implement checks
whose outcome must be repeatable. Do not copy merge eligibility or reviewer-selection
logic into the skill.

## Review isolation and records

`cross-review.ps1` creates a disposable detached worktree at the exact reviewed head and
runs each reviewer from that directory. Relative file reads therefore see the reviewed
commit rather than newer tracked files in the operator's working tree. This is checkout
isolation, not a security sandbox: Git metadata is shared with the main checkout, and the
Codex Windows invocation still relies on a read-only instruction because its sandbox is
unavailable. The disposable worktree is removed after the run even when the reviewer
fails.

Each provider attempt has a configurable timeout (15 minutes by default). A timeout is
an unavailable reviewer outcome, so automatic mode may try the next eligible provider;
it is never treated as approval or rejection.

Every attempt writes redacted metadata to
`.local/cross-reviews/<head-sha>/<timestamp>-<reviewer>.json`. These ignored local
records contain the exact head, model, effort, tier, duration and outcome, but omit
prompts, model output, credentials and repository content. The PR comment remains the
shared record of findings and the exact-head merge marker.

## Verification

Run the harness checks after changing the skill or review scripts:

```powershell
pwsh -NoProfile -File scripts/cross-review-selftest.ps1
uv run --no-project --with pyyaml python scripts/validate_skills.py
```

CI runs both commands before application verification. The skill can also be checked
with the system skill creator's `quick_validate.py` when that bundled tool is available;
CI does not depend on a user-specific Codex installation.
