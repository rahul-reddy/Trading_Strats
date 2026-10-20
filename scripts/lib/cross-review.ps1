# Shared helpers for the cross-model review contract (see AGENTS.md).
# Dot-sourced by scripts/cross-review.ps1 and scripts/merge-pr.ps1; not run directly.

# The reviewer pool, in the order cross-review.ps1 tries them. The contract is
# "reviewed by a model that did not write it", NOT "reviewed by one specific
# model": pinning it to a single provider meant one usage limit blocked every
# Claude-authored PR outright. Any pool member that did not author the commits
# can satisfy the gate, and the marker records which one actually did.
#
# Gemini goes first: commits here are authored by Claude or Codex, never
# Gemini, so it is always eligible and gives an independent third opinion on
# every PR instead of one of the two authoring models reviewing the other.
# Author exclusion still applies after it: if Gemini is unavailable, the
# fallback is whichever of Codex/Claude did NOT write the commits -- never
# the one that did, and never both tried when one already completed a review.
#
# Grok is last on purpose (owner decision, 2026-09-30): a backup reached only
# when every other eligible reviewer is unavailable, so one provider's outage or
# quota never leaves the gate with nobody to ask. It is frontier-class (pinned
# below) and takes a reasoning-effort setting, so it may decide critical diffs
# too. Unlike the CLI reviewers it has no repository access: it reviews from the
# contract and full diff in the prompt, through the xAI API.
$script:ReviewerPool = @('gemini', 'codex', 'claude', 'grok')

function Invoke-BoundedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][AllowEmptyString()][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$StandardInput,
        [Parameter(Mandatory = $true)][ValidateRange(1, 86400)][int]$TimeoutSeconds
    )
    $command = Get-Command $FilePath -ErrorAction Stop
    if (-not $command.Source -or $command.CommandType -notin @('Application', 'ExternalScript')) {
        throw "'$FilePath' must resolve to an application or script file"
    }
    if ($command.CommandType -eq 'ExternalScript') {
        $launcher = (Get-Process -Id $PID).Path
        $arguments = @('-NoProfile', '-File', $command.Source) + $ArgumentList
    }
    elseif ([IO.Path]::GetExtension($command.Source) -in @('.cmd', '.bat')) {
        # npm and similar Windows installers commonly expose command shims. Let
        # PowerShell invoke the shim while ProcessStartInfo still owns the outer
        # process, streams and timeout.
        $launcher = (Get-Process -Id $PID).Path
        $shimRunner = Join-Path $PSScriptRoot 'invoke-command.ps1'
        $argumentJson = ConvertTo-Json -Compress -InputObject @($ArgumentList)
        $encodedArguments = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($argumentJson))
        $arguments = @('-NoProfile', '-File', $shimRunner, $command.Source, $encodedArguments)
    }
    else {
        $launcher = $command.Source
        $arguments = $ArgumentList
    }
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $launcher
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $utf8 = [Text.UTF8Encoding]::new($false)
    $startInfo.StandardInputEncoding = $utf8
    $startInfo.StandardOutputEncoding = $utf8
    $startInfo.StandardErrorEncoding = $utf8
    foreach ($argument in $arguments) { $startInfo.ArgumentList.Add($argument) }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    if (-not $process.Start()) { throw "Could not start '$FilePath'" }
    try {
        $timer = [Diagnostics.Stopwatch]::StartNew()
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $writeTask = $process.StandardInput.WriteAsync($StandardInput)
        $writeCompleted = $false
        try { $writeCompleted = $writeTask.Wait($TimeoutSeconds * 1000) }
        catch [AggregateException] {
            # A reviewer may exit before consuming its prompt (auth/quota errors
            # commonly do). Continue to collect its real exit code and stderr.
            $writeCompleted = $true
        }
        if (-not $writeCompleted) {
            try { $process.Kill($true) } catch { $process.Kill() }
            [void]$process.WaitForExit(5000)
            throw "'$FilePath' exceeded the $TimeoutSeconds-second timeout while receiving input"
        }
        try { $process.StandardInput.Close() } catch [IO.IOException] { }
        $remainingMilliseconds = ($TimeoutSeconds * 1000) - [int]$timer.ElapsedMilliseconds
        if ($remainingMilliseconds -le 0 -or -not $process.WaitForExit($remainingMilliseconds)) {
            try { $process.Kill($true) } catch { $process.Kill() }
            [void]$process.WaitForExit(5000)
            throw "'$FilePath' exceeded the $TimeoutSeconds-second timeout"
        }
        [pscustomobject]@{
            ExitCode = $process.ExitCode
            Stdout   = $stdoutTask.GetAwaiter().GetResult()
            Stderr   = $stderrTask.GetAwaiter().GetResult()
        }
    }
    finally {
        $process.Dispose()
    }
}

# The model each reviewer must use, by tier. None of these CLIs guarantee a
# specific model on their own: Gemini resolves its model from GEMINI_MODEL or
# auto-routes simple prompts to Flash, and Codex takes its default from a
# mutable ~/.codex/config.toml. Either could silently answer with a different
# model than intended, whose APPROVE would still satisfy the merge gate.
# Command-line arguments outrank both, so the invocation pins the model
# explicitly. A pinned model the account cannot use makes the CLI fail, which
# reports the reviewer as unavailable and falls through -- fail-closed.
#
# Codex ran a DIFFERENT, lesser base model (gpt-5.6-sol) on critical-tier
# diffs briefly (2026-09-14); the owner reverted this back to gpt-6-astra
# ("most capable") at every tier the same day, so only effort now varies by
# tier for Codex, same as Gemini and Claude (see Get-ReviewerEffort).
# Update these when the providers rename their models.
$script:ReviewerModels = @{
    critical = @{ codex = 'gpt-6-astra'; gemini = 'gemini-3-pro-preview'; claude = 'opus'; grok = 'grok-4.7' }
    standard = @{ codex = 'gpt-6-astra'; gemini = 'gemini-3-pro-preview'; claude = 'opus'; grok = 'grok-4.7' }
    low      = @{ codex = 'gpt-6-astra'; gemini = 'gemini-3-pro-preview'; claude = 'opus'; grok = 'grok-4.7' }
}

function Get-ReviewerModel {
    param(
        [Parameter(Mandatory = $true)][string]$Reviewer,
        [Parameter(Mandatory = $true)][ValidateSet('critical', 'standard', 'low')][string]$Tier
    )
    if (-not $script:ReviewerModels[$Tier].ContainsKey($Reviewer)) {
        throw "No pinned model for reviewer '$Reviewer' at tier '$Tier'; refusing to review with an unknown model"
    }
    $script:ReviewerModels[$Tier][$Reviewer]
}

# Paths where a bug places or sizes orders wrongly, leaks credentials, or opens
# the review gate. Each strategy is a top-level folder (<strategy>/<package>/...),
# so the patterns cover every strategy, current and future. Kept identical to the
# list in AGENTS.md "Cross-model code review": any touch here gets the critical
# tier, regardless of what else changed alongside it.
$script:CriticalPathPatterns = @(
    '^[^/]+/[^/]+/(runner|strategy|quotes|data|config|risk)\.py$',  # signal, sizing, prices, settings
    '^[^/]+/[^/]+/brokers/',                                         # order transmission
    '^[^/]+/config[^/]*\.toml$',                                     # capital, limits, live switches
    '^[^/]+/deploy/',                                                # server, IAM, secrets, schedules
    '^scripts/',                                                     # this review and merge gate
    '^\.github/workflows/'                                           # the CI check the gate requires
)

# Changes where a lighter review is a defensible trade: prose with no executable
# logic, or ADDITIVE test-only diffs. `.github/` excludes workflows deliberately:
# those are executable CI configuration (secrets exposure, a disabled check), not
# documentation, and a blanket match would have waved them through as low-risk.
$script:LowRiskPathPatterns = @(
    '\.md$',
    '^docs/',
    '^\.github/(?!workflows/)',
    '\.css$'
)
$script:TestPathPatterns = @('^[^/]+/tests/')

# Shared by cross-review.ps1 and merge-pr.ps1 so the path-parsing correctness
# lives in exactly one place. Two things matter here, both found only by
# actually reproducing them against this repository's real git history:
#
# 1. --no-renames: without it, a renamed critical file (tqqq_blend/runner.py ->
#    tqqq_blend/run.py) with edits shows only the NEW path in --name-only
#    output and a "{old => new}" combined string in --numstat, so the rename
#    is invisible to path-pattern classification. With it, the old path is
#    reported as a deletion and the new path as an addition, so both names are
#    matched. GitHub's GraphQL PullRequestFile type has no field for a
#    renamed file's previous path at all, so this can only be fixed via local
#    git, never via `gh pr view --json files`.
# 2. -z (NUL-terminated output): without it, git's default core.quotePath
#    behavior renders a non-ASCII path as an octal-escaped, double-quoted
#    string -- e.g. x/config-café.toml becomes "x/config-caf\303\251.toml" --
#    which then matches no path pattern at all. -z disables that quoting and
#    separates records with NUL instead of newline, which PowerShell's
#    Out-String preserves as embedded NUL characters in one string; splitting
#    on [char]0 recovers the exact original path.
#
# Both gaps were found by cross-review of the commit that introduced tiering,
# each only after the previous one was fixed.
function Get-ChangedFilesAndDeletions {
    param(
        [Parameter(Mandatory = $true)][string]$Base,
        [Parameter(Mandatory = $true)][string]$Head
    )
    $rawNames = (git diff --no-renames --name-only -z "$Base...$Head" | Out-String)
    if ($LASTEXITCODE -ne 0) { throw "git diff --name-only -z '$Base...$Head' failed (exit $LASTEXITCODE)" }
    $files = @($rawNames -split [char]0 | Where-Object { $_.Trim() -ne '' })

    $rawNumstat = (git diff --no-renames --numstat -z "$Base...$Head" | Out-String)
    if ($LASTEXITCODE -ne 0) { throw "git diff --numstat -z '$Base...$Head' failed (exit $LASTEXITCODE)" }
    $deletions = @{}
    foreach ($token in @($rawNumstat -split [char]0 | Where-Object { $_.Trim() -ne '' })) {
        $parts = $token -split "`t"
        if ($parts.Count -ge 3 -and $parts[1] -match '^\d+$') { $deletions[$parts[2]] = [int]$parts[1] }
    }
    [pscustomobject]@{ Files = $files; Deletions = $deletions }
}

# Classifies a changed-file list into 'critical' | 'standard' | 'low', the tier
# Get-ReviewerEffort and Get-EligibleReviewers key off. Critical wins over low:
# a docs edit bundled into a risk.py change still gets full effort. $Deletions
# is a path -> deleted-line-count map (from `git diff --numstat`): a test file
# is low-risk only when it has zero deletions. A test DELETION or weakening is
# exactly the failure AGENTS.md calls blocking ("a missing test for
# risk-affecting behavior"), so it must never be reviewed at reduced effort just
# because the diff happens to touch only test paths. Found by cross-review.
function Get-BlastRadiusTier {
    param(
        [AllowEmptyCollection()][array]$ChangedFiles,
        [Hashtable]$Deletions = @{}
    )
    $files = @($ChangedFiles | Where-Object { $_ })
    if ($files.Count -eq 0) { return 'standard' }
    foreach ($f in $files) {
        foreach ($pattern in $script:CriticalPathPatterns) {
            if ($f -match $pattern) { return 'critical' }
        }
    }
    $allLowRisk = $true
    foreach ($f in $files) {
        $isLowRisk = $false
        foreach ($pattern in $script:LowRiskPathPatterns) {
            if ($f -match $pattern) { $isLowRisk = $true; break }
        }
        if (-not $isLowRisk) {
            foreach ($pattern in $script:TestPathPatterns) {
                # Require a CONFIRMED zero, not a missing entry defaulting to
                # zero: this project never treats absent data as a pass, and a
                # numstat parsing gap must not silently grant low-risk status.
                if ($f -match $pattern -and $Deletions.ContainsKey($f) -and $Deletions[$f] -eq 0) {
                    $isLowRisk = $true; break
                }
            }
        }
        if (-not $isLowRisk) { $allLowRisk = $false; break }
    }
    if ($allLowRisk) { return 'low' }
    return 'standard'
}

# There is deliberately no GitHub-API variant of this (contrast
# Get-AuthorModels / Get-AuthorModelsFromPrCommits). `gh pr view --json files`
# and the underlying GraphQL PullRequestFile type report only a renamed file's
# NEW path, with no field for its previous path at all -- confirmed against the
# live schema. A critical file renamed with edits would then be invisible to a
# GitHub-API-only classification. merge-pr.ps1 fetches the PR locally instead
# and calls Get-BlastRadiusTier directly with `git diff --no-renames` output,
# which reports the old and new paths as a separate deletion and addition.
# A GitHub-API version of this existed here briefly and was removed after a
# cross-review of the change that added it found this gap.

# Per-CLI effort flags by tier. Gemini has no reasoning-effort/thinking-budget
# flag exposed by this CLI version, so it always runs at its own default; only
# Codex and Claude are tuned. A tier/reviewer with no entry runs at the CLI's
# own default rather than guessing at a flag value that might not be valid.
#
# Owner-chosen cost/quality trade-off (2026-09-14, revised same day): Codex
# runs gpt-6-astra at 'low' ("light") effort across every tier -- the
# critical-tier 'high' setting (and the brief gpt-5.6-sol swap) was reverted
# as overkill. Claude's opus confirmed to support up to 'max', used here at
# 'medium' for critical, well below its own ceiling, on purpose. Update if a
# pinned model's effort levels are renamed.
#
# Grok (grok-4.7 via the xAI API's reasoning_effort, confirmed live 2026-09-30
# for 'low' and 'high') runs 'high' except on low-tier diffs: as a last-resort
# reviewer it is rarely reached, so the extra cost buys scrutiny when it matters.
$script:ReviewerEffort = @{
    critical = @{ codex = 'low'; claude = 'medium'; grok = 'high' }
    standard = @{ codex = 'low'; claude = 'medium'; grok = 'high' }
    low      = @{ codex = 'low'; claude = 'low'; grok = 'low' }
}

function Get-ReviewerEffort {
    param(
        [Parameter(Mandatory = $true)][string]$Reviewer,
        [Parameter(Mandatory = $true)][ValidateSet('critical', 'standard', 'low')][string]$Tier
    )
    if ($script:ReviewerEffort[$Tier].ContainsKey($Reviewer)) {
        return $script:ReviewerEffort[$Tier][$Reviewer]
    }
    return $null
}

# Reviewers run with NO MCP servers. Each CLI otherwise loads every server the
# owner registered for everyday use (REA, claude.ai connectors such as Gmail),
# which a read-only review never needs: each one is extra startup time and an
# extra failure mode that would report the reviewer unavailable, and extra
# tools a reviewer could call outside the repository. Codex matters most, since
# it already runs without a sandbox on Windows. Each flag was confirmed live
# (2026-10-16) against a marker-writing server that starts without it and does
# not start with it:
# - claude: --strict-mcp-config with no --mcp-config loads nothing, including
#   claude.ai connectors.
# - gemini: --allowed-mcp-server-names admits only the named servers; the name
#   below matches none.
# - codex: `-c mcp_servers={}` MERGES rather than replaces, so it disables
#   nothing. Each configured server is disabled by name instead
#   (Get-CodexMcpDisableArguments); the names come from `codex mcp list --json`.
# Grok runs over the xAI API and loads no MCP servers at all.
$script:GeminiNoMcpServerName = '__cross_review_no_mcp__'

function Get-ReviewerNoMcpArguments {
    param(
        [Parameter(Mandatory = $true)][string]$Reviewer,
        [AllowEmptyCollection()][string[]]$CodexServerNames = @()
    )
    switch ($Reviewer) {
        'claude' { return @('--strict-mcp-config') }
        'gemini' { return @('--allowed-mcp-server-names', $script:GeminiNoMcpServerName) }
        'codex' { return @(Get-CodexMcpDisableArguments -ServerNames $CodexServerNames) }
        'grok' { return @() }
        default { throw "Unknown reviewer '$Reviewer'" }
    }
}

function Get-CodexMcpDisableArguments {
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$ServerNames)
    $arguments = @()
    foreach ($name in $ServerNames) {
        # -c takes a dotted TOML path, so a name containing a dot or quote would
        # address a different key and leave the real server enabled. Refuse it
        # rather than review with a server that may still load.
        if ($name -notmatch '^[A-Za-z0-9_-]+$') {
            throw "Codex MCP server name '$name' cannot be disabled safely on the command line"
        }
        $arguments += @('-c', "mcp_servers.$name.enabled=false")
    }
    $arguments
}

# Fails (so Codex reports unavailable) if the list cannot be read: reviewing
# with servers that might still load is the state this exists to prevent.
function Get-CodexMcpServerNames {
    param(
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [ValidateRange(1, 86400)][int]$TimeoutSeconds = 60,
        [string]$CodexCommand = 'codex'
    )
    $listing = Invoke-BoundedProcess -FilePath $CodexCommand -ArgumentList @('mcp', 'list', '--json') `
        -WorkingDirectory $WorkingDirectory -StandardInput '' -TimeoutSeconds $TimeoutSeconds
    if ($listing.ExitCode -ne 0) {
        throw "codex mcp list exited $($listing.ExitCode): $($listing.Stderr)"
    }
    try { $servers = $listing.Stdout | ConvertFrom-Json -ErrorAction Stop }
    catch { throw "codex mcp list returned malformed JSON: $($listing.Stdout)" }
    @($servers | ForEach-Object {
        if (-not $_.name) { throw 'codex mcp list returned a server without a name' }
        [string]$_.name
    })
}

function New-CrossReviewMarker {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('claude', 'codex', 'gemini', 'grok')][string]$Reviewer,
        [Parameter(Mandatory = $true)][string]$HeadSha,
        [Parameter(Mandatory = $true)][ValidateSet('APPROVE', 'REQUEST_CHANGES')][string]$Verdict,
        [Parameter(Mandatory = $true)][int]$Blocking
    )
    "<!-- cross-review:$Reviewer head=$HeadSha verdict=$Verdict blocking=$Blocking -->"
}

$script:CrossReviewMarkerPattern =
    '<!-- cross-review:(?<reviewer>claude|codex|gemini|grok) head=(?<head>[0-9a-f]{7,40}) verdict=(?<verdict>APPROVE|REQUEST_CHANGES) blocking=(?<blocking>\d+) -->'

# Which model(s) WROTE these commits, from their Co-Authored-By trailers.
# Returns zero, one or more pool names. Empty means human-authored.
function Get-AuthorModelsFromMessages {
    param([Parameter(Mandatory = $true)][string]$CommitMessages)
    $authors = @()
    if ($CommitMessages -match '(?im)^Co-Authored-By:.*Claude') { $authors += 'claude' }
    if ($CommitMessages -match '(?im)^Co-Authored-By:.*(Codex|OpenAI|GPT)') { $authors += 'codex' }
    if ($CommitMessages -match '(?im)^Co-Authored-By:.*(Gemini|Google)') { $authors += 'gemini' }
    if ($CommitMessages -match '(?im)^Co-Authored-By:.*(Grok|xAI|x\.ai)') { $authors += 'grok' }
    # Return a PLAIN array. Every call site wraps this in @(), and a unary-comma
    # return would make that wrap produce a NESTED array: Authors.Count became 1
    # for a human commit (blocking PRs that need no review) and, far worse,
    # `'claude' -notin $Authors` compared a string against an inner array and
    # never matched, so the authoring model was not excluded and could approve
    # its own work. Found by cross-review of this very change.
    $authors
}

# Local-git variant for interactive use (scripts/cross-review.ps1), where the
# operator already has $Base..$HeadSha checked out or fetched.
function Get-AuthorModels {
    param(
        [Parameter(Mandatory = $true)][string]$Base,
        [Parameter(Mandatory = $true)][string]$HeadSha
    )
    $body = git log "$Base..$HeadSha" --pretty=format:%B 2>&1
    if ($LASTEXITCODE -ne 0) {
        # A failed `git log` here would otherwise fall through as plain text
        # that matches no Co-Authored-By trailer, silently resolving to "no
        # review required" instead of surfacing the real problem -- the same
        # class of fail-open gap a prior cross-review found in the diff/stat
        # generation this function's caller also performs.
        throw "git log '$Base..$HeadSha' failed (exit $LASTEXITCODE): $body"
    }
    Get-AuthorModelsFromMessages -CommitMessages ($body | Out-String)
}

# GitHub-API variant for the merge gate (scripts/merge-pr.ps1), so authorship
# detection never depends on what happens to be checked out or fetched locally.
function Get-AuthorModelsFromPrCommits {
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][array]$Commits)
    $body = ($Commits | ForEach-Object { "$($_.messageHeadline)`n$($_.messageBody)" }) -join "`n"
    Get-AuthorModelsFromMessages -CommitMessages $body
}

# Pool members that did not write the commits, in pool order.
function Get-EligibleReviewers {
    param(
        [AllowEmptyCollection()][array]$Authors,
        [ValidateSet('critical', 'standard', 'low')][string]$Tier = 'standard'
    )
    # Flatten defensively: a nested array here silently disables author exclusion.
    $Authors = @($Authors | ForEach-Object { $_ } | Where-Object { $null -ne $_ })
    $pool = $script:ReviewerPool
    if ($Tier -eq 'critical') {
        # Gemini's CLI has no effort control (see Get-ReviewerEffort), so with the
        # pool in Gemini-first order it would normally complete the review alone
        # on every PR -- including one touching a broker adapter -- since a completed
        # review stops the search. That leaves the highest-stakes diffs decided by
        # the one reviewer that never gets escalated, and never checked by a
        # second model. Restrict critical-tier eligibility to reviewers this
        # module can actually escalate. Found by cross-review of this change.
        $pool = $pool | Where-Object { $null -ne (Get-ReviewerEffort -Reviewer $_ -Tier 'critical') }
    }
    $pool | Where-Object { $_ -notin $Authors }
}

# Finds the newest marker comment for $Reviewer among $Comments (from
# `gh pr view --json comments`). Returns $null if none exists.
function Find-CrossReviewComment {
    param(
        # AllowEmptyCollection: PowerShell's Mandatory validation on an [array]
        # parameter rejects an explicitly empty array as if it were missing
        # unless this is present -- a PR with zero comments would otherwise
        # throw here instead of correctly reporting "no review found". Found by
        # a prior cross-review of this exact script.
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][array]$Comments,
        [Parameter(Mandatory = $true)][ValidateSet('claude', 'codex', 'gemini', 'grok')][string]$Reviewer,
        # Filter by head BEFORE picking the newest. Picking the newest comment
        # across all heads and checking its SHA afterwards let a late-arriving
        # review of an OLDER head hide an unresolved rejection of the current
        # one: the stale comment won "newest", was dismissed as stale, and the
        # real blocking verdict on this head was never consulted. Concurrent
        # cross-review runs can post out of order, so this is reachable.
        # Found by cross-review of this change.
        [string]$HeadSha
    )
    $found = @()
    foreach ($c in $Comments) {
        $m = [regex]::Match($c.body, $script:CrossReviewMarkerPattern)
        if ($m.Success -and $m.Groups['reviewer'].Value -eq $Reviewer -and
            ($HeadSha -eq '' -or $m.Groups['head'].Value -eq $HeadSha)) {
            $found += [pscustomobject]@{
                CreatedAt = [datetime]$c.createdAt
                Head      = $m.Groups['head'].Value
                Verdict   = $m.Groups['verdict'].Value
                Blocking  = [int]$m.Groups['blocking'].Value
            }
        }
    }
    if ($found.Count -eq 0) { return $null }
    $found | Sort-Object CreatedAt -Descending | Select-Object -First 1
}

# The merge gate. Satisfied when ANY eligible reviewer -- a pool member that did
# not author the commits -- has a clean, same-head verdict. Human-authored
# commits need no cross-review at all.
function Test-CrossReviewSatisfied {
    param(
        [array]$Authors,
        [array]$Comments,
        [Parameter(Mandatory = $true)][string]$HeadSha,
        # Mandatory in effect: merge-pr.ps1 always computes this from the PR's
        # actual changed files. It defaults to 'standard' only so existing
        # direct unit tests of this function do not all need updating; a
        # merge decision must never rely on that default. A prior cross-review
        # found this parameter missing entirely, which meant the gate always
        # evaluated 'standard' and so ever treated Gemini as eligible to decide
        # a critical-tier diff -- exactly the case Get-EligibleReviewers's
        # -Tier 'critical' branch exists to prevent, in the one function that
        # actually blocks or allows a merge.
        [ValidateSet('critical', 'standard', 'low')][string]$Tier = 'standard'
    )
    # Normalize defensively against $null, independent of whether every call site
    # remembers to wrap a function's result in @(...). Note @($null) is a
    # ONE-element array containing null, not an empty array, so the null check
    # must come first -- this exact PowerShell pitfall is what caused the
    # original bug a prior cross-review of this script found.
    # Flatten as well as null-guard: a nested $Authors would make the
    # human-authored short-circuit and author exclusion both silently wrong.
    $Authors = @($Authors | ForEach-Object { $_ } | Where-Object { $null -ne $_ })
    if ($null -eq $Comments) { $Comments = @() } else { $Comments = @($Comments) }
    if ($Authors.Count -eq 0) { return $true }

    $eligible = Get-EligibleReviewers -Authors $Authors -Tier $Tier
    if ($null -eq $eligible) { $eligible = @() } else { $eligible = @($eligible) }
    if ($eligible.Count -eq 0) {
        Write-Host "Every reviewer in the pool co-authored these commits, or is excluded from the '$Tier' tier; none can review them."
        return $false
    }
    # An outstanding rejection from ANY eligible reviewer blocks, even if another
    # approved. Accepting the first approval found would let a rejected PR merge
    # by re-running against a different model until one said yes. Pushing a fix
    # moves the head, which makes the old rejection stale and stops blocking --
    # so the way past a rejection is to address it, not to re-roll the reviewer.
    $problems = @()
    $approvals = @()
    foreach ($r in $eligible) {
        $verdict = Find-CrossReviewComment -Comments $Comments -Reviewer $r -HeadSha $HeadSha
        if ($null -eq $verdict) { $problems += "$r has not reviewed this head"; continue }
        if ($verdict.Blocking -gt 0 -or $verdict.Verdict -ne 'APPROVE') {
            Write-Host "$r has $($verdict.Blocking) unresolved blocking finding(s) on $HeadSha."
            Write-Host "Address them and re-run; another reviewer's approval cannot override this."
            return $false
        }
        $approvals += $r
    }
    if ($approvals.Count -gt 0) {
        Write-Host "Cross-review satisfied by $($approvals -join ', ') on $HeadSha."
        return $true
    }
    Write-Host "No eligible reviewer has approved $HeadSha :"
    foreach ($p in $problems) { Write-Host "  - $p" }
    Write-Host "Run: scripts/cross-review.ps1 -Number <PR>"
    return $false
}

# The xAI key for the Grok reviewer: the process environment first, then the
# repository's own .env (the same XAI_API_KEY the app reads). Never logged.
function Get-XaiApiKey {
    param([Parameter(Mandatory = $true)][string]$RepositoryRoot)
    if ($env:XAI_API_KEY) { return $env:XAI_API_KEY }
    $envFile = Join-Path $RepositoryRoot '.env'
    if (-not (Test-Path -LiteralPath $envFile)) { return $null }
    foreach ($line in Get-Content -LiteralPath $envFile) {
        $m = [regex]::Match($line, '^\s*XAI_API_KEY\s*=\s*(.*?)\s*$')
        if ($m.Success) {
            $value = $m.Groups[1].Value.Trim('"', "'")
            if ($value) { return $value }
        }
    }
    return $null
}

# One xAI chat completion, streamed. A non-streamed request sends nothing while
# Grok reasons, and the connection is dropped at 60 s of silence ("response
# ended prematurely"), long before a real review finishes. Streaming keeps bytes flowing. The whole call --
# headers, error bodies and the stream -- is bounded by $TimeoutSeconds. Fails
# closed on any HTTP error, a truncated stream, or an answer from any model other
# than $ExpectedModel (the same reason the CLI reviewers pin their model).
function Invoke-XaiChatStream {
    param(
        [Parameter(Mandatory = $true)][string]$ApiKey,
        [Parameter(Mandatory = $true)][System.Collections.IDictionary]$Request,
        [Parameter(Mandatory = $true)][string]$ExpectedModel,
        [Parameter(Mandatory = $true)][ValidateRange(1, 86400)][int]$TimeoutSeconds,
        [string]$Uri = 'https://api.x.ai/v1/chat/completions'
    )
    $body = [ordered]@{}
    foreach ($key in $Request.Keys) { $body[$key] = $Request[$key] }
    $body['stream'] = $true
    $cancel = [Threading.CancellationTokenSource]::new([TimeSpan]::FromSeconds($TimeoutSeconds))
    $client = [Net.Http.HttpClient]::new()
    $client.Timeout = [Threading.Timeout]::InfiniteTimeSpan
    $message = $null
    $response = $null
    $reader = $null
    try {
        $message = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Post, $Uri)
        $message.Headers.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', $ApiKey)
        $message.Content = [Net.Http.StringContent]::new(
            ($body | ConvertTo-Json -Depth 8), [Text.Encoding]::UTF8, 'application/json')
        try {
            $response = $client.SendAsync($message, [Net.Http.HttpCompletionOption]::ResponseHeadersRead,
                $cancel.Token).GetAwaiter().GetResult()
            if (-not $response.IsSuccessStatusCode) {
                $detail = $response.Content.ReadAsStringAsync($cancel.Token).GetAwaiter().GetResult()
                throw "HTTP $([int]$response.StatusCode): $detail"
            }
            $reader = [IO.StreamReader]::new($response.Content.ReadAsStream($cancel.Token))
            $text = [Text.StringBuilder]::new()
            $model = $null
            $finished = $false
            while ($true) {
                $line = $reader.ReadLineAsync($cancel.Token).AsTask().GetAwaiter().GetResult()
                if ($null -eq $line) { break }
                if (-not $line.StartsWith('data:')) { continue }
                $data = $line.Substring(5).Trim()
                if ($data -eq '[DONE]') { $finished = $true; break }
                $chunk = $data | ConvertFrom-Json
                if ($chunk.model) { $model = $chunk.model }
                foreach ($choice in @($chunk.choices)) {
                    if ($choice.delta.content) { [void]$text.Append($choice.delta.content) }
                }
            }
        }
        catch [OperationCanceledException] {
            throw "exceeded the $TimeoutSeconds-second timeout"
        }
        if (-not $finished) { throw 'the response stream ended before completion' }
        if ($model -ne $ExpectedModel) {
            throw "answered as '$model', not the pinned '$ExpectedModel'"
        }
        [pscustomobject]@{ Model = $model; Content = $text.ToString() }
    }
    finally {
        # Innermost first, so nothing is disposed while something still reads it.
        if ($reader) { $reader.Dispose() }
        if ($response) { $response.Dispose() }
        if ($message) { $message.Dispose() }
        $client.Dispose()
        $cancel.Dispose()
    }
}
