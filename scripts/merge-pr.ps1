param([Parameter(Mandatory=$true)][int]$Number)
$ErrorActionPreference = 'Stop'
# Operational guard; not a replacement for server-side protection.
. "$PSScriptRoot/lib/cross-review.ps1"

$prJson = gh pr view $Number --json headRefOid,baseRefName,state,isDraft,mergeable,statusCheckRollup,comments,commits
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect PR' }
$pr = $prJson | ConvertFrom-Json
if ($pr.state -ne 'OPEN' -or $pr.isDraft -or $pr.mergeable -ne 'MERGEABLE') {
    throw 'PR must be open, ready and mergeable'
}
$verify = @($pr.statusCheckRollup | Where-Object { $_.name -eq 'Verify strategies' })
if ($verify.Count -eq 0 -or @($verify | Where-Object { $_.conclusion -ne 'SUCCESS' }).Count -gt 0) {
    throw 'Verify strategies must pass on the current PR head'
}

# Cross-model review gate (see AGENTS.md "Cross-model code review"): a PR whose
# commits carry an AI Co-Authored-By trailer needs an APPROVE verdict from some
# eligible reviewer, posted as a marker comment matching this exact head SHA,
# before it may merge. scripts/cross-review.ps1 produces that comment. Authorship
# is read from the GitHub API, not local git, so it never depends on what happens
# to be checked out locally. The tier is independently derived here too -- never
# trusted from a self-reported value cross-review.ps1 happened to compute earlier
# -- but via a local fetch and Get-ChangedFilesAndDeletions, not `gh pr view
# --json files`: GitHub's file list has no field for a renamed file's PREVIOUS
# path at all (confirmed against the GraphQL schema), so a critical file
# renamed with edits would be invisible to a GitHub-API-only classification.
# A prior cross-review found this gate's missing tier check entirely (it
# always evaluated 'standard', so the critical-tier Gemini exclusion existed
# only in the advisory script); once added, further rounds found the rename
# gap in the GitHub-API path used to fix it, a mutable-FETCH_HEAD race, and a
# quoted-path gap in the git parsing that replaced it -- see
# Get-ChangedFilesAndDeletions's own comment for the last two.
git fetch origin $pr.baseRefName --quiet
if ($LASTEXITCODE -ne 0) { throw "Could not fetch base branch $($pr.baseRefName)" }
git fetch origin "pull/$Number/head" --quiet
if ($LASTEXITCODE -ne 0) { throw "Could not fetch PR #$Number head" }
$baseSha = (git rev-parse "origin/$($pr.baseRefName)").Trim()
if ($LASTEXITCODE -ne 0) { throw "Could not resolve base branch $($pr.baseRefName)" }
# Resolve and pin FETCH_HEAD to a SHA immediately, then use that SHA (never the
# literal ref name) in the diff commands below. FETCH_HEAD is a mutable ref: a
# concurrent fetch in this shared checkout -- another agent, a background
# process -- can overwrite it between here and the diff calls, which would
# silently classify a critical PR as standard/low while --match-head-commit
# still targets the correct (unchanged) remote head, since the remote PR
# itself never moved. Found by cross-review of this change.
$fetchedSha = (git rev-parse 'FETCH_HEAD').Trim()
if ($LASTEXITCODE -ne 0) { throw "Could not resolve fetched PR #$Number head" }
if ($fetchedSha -ne $pr.headRefOid) {
    throw "Fetched head $fetchedSha does not match PR #$Number's reported head $($pr.headRefOid) (it may have been pushed to again; re-run)"
}
$changed = Get-ChangedFilesAndDeletions -Base $baseSha -Head $fetchedSha
$tier = Get-BlastRadiusTier -ChangedFiles $changed.Files -Deletions $changed.Deletions
Write-Host "Blast radius: $tier ($($changed.Files.Count) file(s) changed)"

$authorModels = @(Get-AuthorModelsFromPrCommits -Commits $pr.commits)
if (-not (Test-CrossReviewSatisfied -Authors $authorModels -Comments $pr.comments -HeadSha $pr.headRefOid -Tier $tier)) {
    throw "Cross-model review is not satisfied for head $($pr.headRefOid). Run scripts/cross-review.ps1 -Number $Number and address any blocking findings."
}

gh pr merge $Number --squash --delete-branch --match-head-commit $pr.headRefOid
if ($LASTEXITCODE -ne 0) { throw 'Merge refused or failed' }
