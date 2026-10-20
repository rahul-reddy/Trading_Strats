param(
    [string]$Base = 'main',
    [string]$Head = (git rev-parse --abbrev-ref HEAD),
    [Nullable[int]]$Number,
    [ValidateSet('auto', 'claude', 'codex', 'gemini', 'grok', 'all')]
    [string]$Reviewer = 'auto',
    [string]$Rebuttal = '',
    [ValidateRange(1, 86400)]
    [int]$TimeoutSeconds = 900
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/lib/cross-review.ps1"

# See AGENTS.md "Cross-model code review" for the contract this script implements:
# a commit range is reviewed by the model that did NOT author it, and merge-pr.ps1
# refuses to merge until that reviewer's newest verdict on the current head has zero
# blocking findings. This script produces that verdict; it never merges anything.

$expectedHeadSha = $null
if ($Number) {
    # Resolve Base/Head from the PR itself rather than trusting caller-supplied
    # values, so an approval marker can never be posted for a different diff than
    # the one actually reviewed (a prior cross-review of this same script found
    # this exact gap: -Base could be set to equal -Head to review nothing).
    $prMeta = gh pr view $Number --json baseRefName,headRefOid | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw "Could not inspect PR #$Number" }
    git fetch origin $prMeta.baseRefName --quiet
    if ($LASTEXITCODE -ne 0) { throw "Could not fetch base branch $($prMeta.baseRefName)" }
    git fetch origin "pull/$Number/head" --quiet
    if ($LASTEXITCODE -ne 0) { throw "Could not fetch PR #$Number head" }
    $Base = "origin/$($prMeta.baseRefName)"
    $Head = 'FETCH_HEAD'
    $expectedHeadSha = $prMeta.headRefOid
}

$headSha = (git rev-parse $Head).Trim()
if ($LASTEXITCODE -ne 0) { throw "Could not resolve head '$Head'" }
if ($expectedHeadSha -and $headSha -ne $expectedHeadSha) {
    throw "Resolved head $headSha does not match PR #$Number's actual head $expectedHeadSha (it may have been pushed to again; re-run)"
}
$baseSha = (git rev-parse $Base).Trim()
if ($LASTEXITCODE -ne 0) { throw "Could not resolve base '$Base'" }

# @(...) forces array context on the assignment: a PowerShell function that emits
# zero pipeline objects (the human-authored/no-trailer case) would otherwise leave
# this as $null rather than an empty array, and a prior cross-review of this same
# script found that crashes the gate's array parameter before its own check.
$authors = @(Get-AuthorModels -Base $baseSha -HeadSha $headSha)
if ($Reviewer -notin @('auto', 'all') -and $Reviewer -in $authors) {
    throw "$Reviewer co-authored these commits and cannot review its own work"
}
if ($Reviewer -eq 'auto' -and $authors.Count -eq 0) {
    Write-Host "No AI Co-Authored-By trailer found on $Base..$Head ($headSha)."
    Write-Host "Cross-model review is not required for human-authored commits."
    Write-Host "Pass -Reviewer codex|claude|gemini|grok|all to request one anyway."
    exit 0
}

$diff = git diff "$baseSha...$headSha" | Out-String
# $ErrorActionPreference = 'Stop' does not make a native command's nonzero exit
# code terminate the script; without this check a broken `git diff` would send
# an empty/partial diff to the reviewer, which could still return APPROVE --
# an approval marker for a diff that was never actually reviewed. Found by a
# prior cross-review of this exact script.
if ($LASTEXITCODE -ne 0) { throw "git diff '$baseSha...$headSha' failed (exit $LASTEXITCODE); refusing to review an incomplete diff" }
$stat = git log "$baseSha..$headSha" --stat --oneline | Out-String
if ($LASTEXITCODE -ne 0) { throw "git log '$baseSha..$headSha' --stat failed (exit $LASTEXITCODE); refusing to review with incomplete context" }
$changed = Get-ChangedFilesAndDeletions -Base $baseSha -Head $headSha
$changedFiles = $changed.Files
# Unresolved (empty) never lowers effort: Get-BlastRadiusTier already treats an
# empty list as 'standard', not 'low'.
$tier = Get-BlastRadiusTier -ChangedFiles $changedFiles -Deletions $changed.Deletions
Write-Host "Blast radius: $tier ($($changedFiles.Count) file(s) changed)"

# Compute the TRUE eligible set unconditionally, even for an explicit -Reviewer,
# so that name can be checked against it. Assign directly rather than through
# @(switch ...): Get-EligibleReviewers returns its array via the `,` operator so
# an empty result survives the pipeline, which means the switch emits ONE
# object -- the array -- and @() around that yields a nested array whose single
# element is the whole list. The loop then "ran" a reviewer named
# "gemini codex claude".
$eligible = Get-EligibleReviewers -Authors $authors -Tier $tier
# $null-check BEFORE @(): @($null) is a ONE-element array containing null, not
# an empty array (the exact pitfall this file already documents elsewhere).
# Get-EligibleReviewers's pipeline emits zero objects on a fully-excluded pool
# (now reachable: a critical-tier diff whose commits are co-authored by BOTH
# Codex and Claude excludes gemini for the tier AND both authors, leaving
# nothing), which a bare @() around that $null would hide from this check.
# Found by cross-review of this change.
if ($null -eq $eligible) { $eligible = @() } else { $eligible = @($eligible) }

if ($Reviewer -notin @('auto', 'all')) {
    # An earlier version compared $Reviewer against $candidates here, but
    # $candidates in this branch was always just @($Reviewer) -- comparing a
    # value to an array containing only itself can never fail, so an explicit
    # -Reviewer gemini silently bypassed critical-tier exclusion entirely.
    # Compare against the independently computed $eligible instead. Found by
    # cross-review of this change.
    if ($Reviewer -notin $eligible) {
        $reason = if ($Reviewer -in $authors) { 'co-authored these commits' } else { "is not eligible for the '$tier' tier" }
        throw "$Reviewer $reason and cannot review this diff"
    }
    $candidates = @($Reviewer)
}
else {
    $candidates = $eligible
    if ($candidates.Count -eq 0) {
        throw "Every reviewer in the pool co-authored these commits, or is excluded from the '$tier' tier; none can review them"
    }
}

$schema = @'
Respond with ONLY a single-line JSON object, no markdown fences, no prose before or after it, matching exactly this shape:
{"verdict":"APPROVE" or "REQUEST_CHANGES","findings":[{"severity":"blocking" or "advisory","file":"path","line":123,"summary":"one sentence","detail":"specifics: reachable path, reproduction, concrete failure"}]}
Use "blocking" ONLY for: a correctness bug, a violation of a stated risk/safety boundary in AGENTS.md, a security issue, or a missing test for risk-affecting behavior you identified.
Use "advisory" for style, naming, simplification, or non-critical suggestions. Advisory findings never block merge; still report them.
If you find nothing, use an empty findings array and verdict APPROVE. Do not soften a real blocking finding into advisory to be agreeable.
The verdict field must agree with the findings: use REQUEST_CHANGES if and only if at least one finding has severity "blocking".
'@

$rebuttalBlock = ''
if ($Rebuttal) {
    $rebuttalBlock = "`n=== Author's rebuttal to a prior review ===`nWeigh this, but reach your own independent verdict; do not defer to it just because it was offered.`n$Rebuttal`n"
}

# Runs one reviewer and returns its validated verdict object, or throws if the
# reviewer could not be run or did not return a usable review. The caller treats
# a throw as "this reviewer is unavailable" and falls through to the next.
function Invoke-Reviewer {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$PromptPath,
        [Parameter(Mandatory = $true)][ValidateSet('critical', 'standard', 'low')][string]$Tier,
        [Parameter(Mandatory = $true)][string]$ReviewRoot,
        [Parameter(Mandatory = $true)][ValidateRange(1, 86400)][int]$TimeoutSeconds
    )
    $model = Get-ReviewerModel -Reviewer $Name -Tier $Tier
    # $null when the CLI has no exposed effort control or the
    # tier has no mapped value; the invocation then omits the flag and the CLI
    # uses its own default rather than being passed a guessed-at value.
    $effort = Get-ReviewerEffort -Reviewer $Name -Tier $Tier
    $outFile = $null
    $promptText = Get-Content $PromptPath -Raw
    try {
        switch ($Name) {
            'codex' {
                # Codex's sandbox backend is disabled on Windows, so reading the
                # repository at all requires the bypass. See AGENTS.md.
                $outFile = New-TemporaryFile
                # @() at the call: an empty listing emits nothing, which a bare
                # assignment turns into $null and a parameter refuses. Found by
                # cross-review of this change.
                $codexServers = @(Get-CodexMcpServerNames -WorkingDirectory $ReviewRoot)
                $arguments = @('exec', '-m', $model)
                if ($effort) { $arguments += @('-c', "model_reasoning_effort=`"$effort`"") }
                $arguments += @(Get-ReviewerNoMcpArguments -Reviewer codex -CodexServerNames $codexServers)
                $arguments += @('--dangerously-bypass-approvals-and-sandbox', '-o', $outFile, '-')
                $result = Invoke-BoundedProcess -FilePath 'codex' -ArgumentList $arguments `
                    -WorkingDirectory $ReviewRoot -StandardInput $promptText -TimeoutSeconds $TimeoutSeconds
                $exitCode = $result.ExitCode
                $raw = Get-Content $outFile -Raw
            }
            'claude' {
                $arguments = @('-p', '--restricted', '--allowedTools',
                    'Read Grep Glob Bash(git diff*) Bash(git show*) Bash(git log*) Bash(git blame*)',
                    '--model', $model) + @(Get-ReviewerNoMcpArguments -Reviewer claude)
                if ($effort) { $arguments += @('--effort', $effort) }
                $result = Invoke-BoundedProcess -FilePath 'claude' -ArgumentList $arguments `
                    -WorkingDirectory $ReviewRoot -StandardInput $promptText -TimeoutSeconds $TimeoutSeconds
                $exitCode = $result.ExitCode
                $raw = $result.Stdout
            }
            'gemini' {
                # Antigravity CLI on the owner's Google login. --mode plan is its
                # read-only mode: confirmed 2026-10-20 that a headless plan-mode run
                # told to write a file, delete one and run a shell command did none
                # of them (file reads still work). --sandbox adds terminal
                # restrictions. Never pass --dangerously-skip-permissions. The prompt
                # goes in on stdin as one stream-json message.
                # Headless plan mode denies every shell command, and a denied command
                # ends the turn with an empty answer. Run from the disposable worktree,
                # Gemini's first move was `git status`, so without this preamble every
                # review came back empty (confirmed 2026-10-20; with it, two runs used
                # only file reads and returned a verdict).
                $promptText = 'Shell commands are disabled in this review: any command, including git, ' +
                    'is denied and ends the review with no result. Do not run commands. The contract and ' +
                    'the complete diff are below; read other files in the working directory with your ' +
                    "file-reading tools if you need context.`n`n" + $promptText
                $agy = Resolve-AgyCommand
                Assert-AgyHasNoExtensions -AgyCommand $agy -WorkingDirectory $ReviewRoot
                $arguments = @('--model', $model, '--mode', 'plan', '--sandbox',
                    '--input-format', 'stream-json', '--output-format', 'stream-json')
                if ($effort) { $arguments += @('--effort', $effort) }
                # One retry, only for agy's transient "stream was interrupted" error
                # (seen on TenFold#141, 2026-10-20). Same reviewer, same head: it cannot
                # turn into reviewer-shopping, and any other failure still falls through.
                for ($attempt = 1; $attempt -le 2; $attempt++) {
                    $result = Invoke-BoundedProcess -FilePath $agy -ArgumentList $arguments `
                        -WorkingDirectory $ReviewRoot -StandardInput (New-AgyStreamInput -Prompt $promptText) `
                        -TimeoutSeconds $TimeoutSeconds
                    $exitCode = $result.ExitCode
                    if ($exitCode -ne 0) { $raw = $result.Stdout; break }
                    try {
                        $raw = Get-AgyStreamResult -Stdout $result.Stdout -ExpectedModel $model
                        break
                    }
                    catch {
                        if ($attempt -eq 2 -or $_.Exception.Message -notmatch 'stream was interrupted') { throw }
                        Write-Host '  gemini: stream interrupted; retrying once.'
                    }
                }
            }
            'grok' {
                # No CLI: the xAI API answers from the prompt alone, so it has no
                # repository access at all (stricter than the CLIs' read-only
                # modes). The key comes from the environment or this repository's
                # .env and is never echoed; missing it reports unavailable.
                $apiKey = Get-XaiApiKey -RepositoryRoot (Join-Path $PSScriptRoot '..')
                if (-not $apiKey) { throw 'XAI_API_KEY is not set in the environment or .env' }
                $request = [ordered]@{
                    model    = $model
                    messages = @(
                        @{ role = 'system'; content = ('You have no file, tool or repository access in ' +
                            'this review. Judge only from the contract and the full diff provided.') }
                        @{ role = 'user'; content = $promptText }
                    )
                }
                if ($effort) { $request.reasoning_effort = $effort }
                try {
                    # Also fails closed on a silent model substitution, the same
                    # reason the CLI reviewers pin their model on the command line.
                    $response = Invoke-XaiChatStream -ApiKey $apiKey -Request $request `
                        -ExpectedModel $model -TimeoutSeconds $TimeoutSeconds
                }
                catch {
                    throw "grok request failed: $($_.Exception.Message)"
                }
                $exitCode = 0
                $raw = $response.Content
            }
            default { throw "Unknown reviewer '$Name'" }
        }
    }
    finally {
        if ($outFile) { Remove-Item $outFile -ErrorAction SilentlyContinue }
    }
    if ($exitCode -ne 0) {
        $diagnostic = (($result.Stderr + ' ' + $raw) -replace '\s+', ' ').Trim()
        throw "$Name exited $exitCode. Output: $diagnostic"
    }
    if ([string]::IsNullOrWhiteSpace($raw)) {
        throw "$Name completed without a review result"
    }
    $jsonMatch = [regex]::Match($raw, '\{.*\}', [Text.RegularExpressions.RegexOptions]::Singleline)
    if (-not $jsonMatch.Success) { throw "$Name returned no JSON object. Output: $raw" }
    try { $parsed = $jsonMatch.Value | ConvertFrom-Json -ErrorAction Stop }
    catch { throw "$Name returned malformed JSON. Output: $raw" }

    # Validate the full schema rather than trusting whatever parsed. A bare `{}`
    # or an unrecognized verdict/severity token must fail loudly, never silently
    # count as zero findings and an implicit APPROVE -- a prior cross-review of
    # this same script found exactly that gap.
    if ($parsed.verdict -notin @('APPROVE', 'REQUEST_CHANGES')) {
        throw "$Name returned an unrecognized or missing verdict. Output: $raw"
    }
    $findings = @($parsed.findings)
    foreach ($f in $findings) {
        if ($f.severity -notin @('blocking', 'advisory') -or -not $f.file -or -not $f.summary) {
            throw "$Name produced a finding with an unrecognized severity or a missing field. Output: $raw"
        }
    }
    if ($parsed.verdict -eq 'REQUEST_CHANGES' -and
        @($findings | Where-Object { $_.severity -eq 'blocking' }).Count -eq 0) {
        # A rejection is a completed review, so it must NOT throw: throwing would
        # mark the reviewer "unavailable" and fall through to another model, which
        # is exactly the reviewer-shopping the pool is supposed to prevent. Treat
        # the inconsistency conservatively as blocking instead. Advisory finding
        # from a cross-review of this change.
        $parsed.findings = @($findings) + @([pscustomobject]@{
            severity = 'blocking'
            file     = 'cross-review'
            line     = 0
            summary  = "$Name returned REQUEST_CHANGES without marking any finding blocking"
            detail   = ('The reviewer rejected this head but its findings were all advisory. ' +
                'Treated as blocking rather than retried against another model, so a ' +
                'rejection can never be escaped by falling through. Re-run after addressing ' +
                'the advisory findings, or use -Rebuttal if the rejection is mistaken.')
        })
    }
    $parsed
}

function Write-ReviewAudit {
    param(
        [Parameter(Mandatory = $true)][string]$ReviewerName,
        [Parameter(Mandatory = $true)][string]$Outcome,
        [Parameter(Mandatory = $true)][datetime]$StartedAt,
        [string]$Verdict = '',
        [string]$Message = ''
    )
    try {
        $auditDirectory = Join-Path $PSScriptRoot "../.local/cross-reviews/$headSha"
        New-Item -ItemType Directory -Force -Path $auditDirectory | Out-Null
        $endedAt = [datetime]::UtcNow
        $stamp = $StartedAt.ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
        $record = [ordered]@{
            schema_version = 1
            base_sha       = $baseSha
            head_sha       = $headSha
            tier           = $tier
            reviewer       = $ReviewerName
            model          = Get-ReviewerModel -Reviewer $ReviewerName -Tier $tier
            effort         = Get-ReviewerEffort -Reviewer $ReviewerName -Tier $tier
            started_at     = $StartedAt.ToString('o')
            ended_at       = $endedAt.ToString('o')
            duration_ms    = [math]::Round(($endedAt - $StartedAt).TotalMilliseconds)
            timeout_seconds = $TimeoutSeconds
            outcome        = $Outcome
            verdict        = $Verdict
            message        = $Message
        }
        $record | ConvertTo-Json -Depth 4 | Set-Content `
            -Path (Join-Path $auditDirectory "$stamp-$ReviewerName.json") -Encoding utf8
    }
    catch {
        # Audit records improve local observability; inability to write an ignored
        # record must not convert a real verdict into failure or disable fallback.
        Write-Warning "Could not write local review audit: $($_.Exception.Message)"
    }
}

$reviewContainer = Join-Path ([IO.Path]::GetTempPath()) ("trading-strats-review-" + [guid]::NewGuid())
$worktreeAdded = $false
$promptFile = New-TemporaryFile
$allFindings = @()
$verdicts = @{}
$reviewers = @()
try {
    git worktree add --detach --force $reviewContainer $headSha --quiet
    if ($LASTEXITCODE -ne 0) { throw "Could not create an isolated worktree for $headSha" }
    $worktreeAdded = $true
    $agents = Get-Content -Raw (Join-Path $reviewContainer 'AGENTS.md')
    $prompt = "You are an independent code reviewer for the Trading_Strats repository, automated " +
        "trading strategies that place real broker orders. You did not write this diff. Do not modify, stage, commit " +
        "or push any files; you are read-only. Your working directory is a disposable checkout " +
        "pinned to the exact reviewed head $headSha. Review strictly against this project's own " +
        "contributor contract below, plus ordinary correctness.`n`n" +
        "=== AGENTS.md ===`n$agents`n`n" +
        "=== Commit range $Base..$Head ($headSha) ===`n$stat`n" +
        "=== Full diff ===`n$diff`n" +
        $rebuttalBlock + "`n$schema"
    Set-Content -Path $promptFile -Value $prompt -Encoding utf8 -NoNewline
    $unavailable = @()
    foreach ($r in $candidates) {
        $rEffort = Get-ReviewerEffort -Reviewer $r -Tier $tier
        $effortNote = if ($rEffort) { ", effort $rEffort" } else { '' }
        Write-Host "Running $r cross-review ($(Get-ReviewerModel -Reviewer $r -Tier $tier)$effortNote) of $Base..$Head ($headSha)..."
        $startedAt = [datetime]::UtcNow
        try {
            $parsed = Invoke-Reviewer -Name $r -PromptPath $promptFile -Tier $tier `
                -ReviewRoot $reviewContainer -TimeoutSeconds $TimeoutSeconds
        }
        catch {
            # A reviewer that could not run is not an approval and not a
            # rejection. Fall through to the next eligible model so one
            # provider's usage limit cannot block the PR outright.
            Write-Host "  $r is unavailable: $($_.Exception.Message)"
            $unavailable += $r
            # Store only the failure class locally. CLI output may echo repository
            # content or provider diagnostics, so it belongs neither in this
            # redacted audit record nor in a durable ignored file by default.
            $auditMessage = ($_.Exception.Message -split 'Output:', 2)[0].Trim()
            Write-ReviewAudit -ReviewerName $r -Outcome 'unavailable' -StartedAt $startedAt `
                -Message $auditMessage
            continue
        }
        Write-ReviewAudit -ReviewerName $r -Outcome 'completed' -StartedAt $startedAt `
            -Verdict $parsed.verdict
        $reviewers += $r
        $verdicts[$r] = $parsed.verdict
        foreach ($f in @($parsed.findings)) {
            $allFindings += [pscustomobject]@{
                Reviewer = $r
                Severity = $f.severity
                File     = $f.file
                Line     = $f.line
                Summary  = $f.summary
                Detail   = $f.detail
            }
        }
        # One completed review satisfies the contract. A REQUEST_CHANGES is a
        # completed review, not a failure, so we stop there too: shopping for a
        # friendlier reviewer after a rejection would defeat the whole gate.
        if ($Reviewer -ne 'all') { break }
    }
    if ($reviewers.Count -eq 0) {
        throw ("No eligible reviewer could complete a review (tried: $($unavailable -join ', ')). " +
            "Nothing is approved. Re-run when a reviewer is available.")
    }
    if ($unavailable.Count -gt 0) {
        Write-Host "Fell back past unavailable reviewer(s): $($unavailable -join ', ')"
    }
}
finally {
    Remove-Item $promptFile -ErrorAction SilentlyContinue
    if ($worktreeAdded) {
        git worktree remove --force $reviewContainer 2>$null
        if ($LASTEXITCODE -ne 0 -and (Test-Path $reviewContainer)) {
            Write-Warning "Could not remove disposable review worktree $reviewContainer"
        }
    }
    elseif (Test-Path $reviewContainer) {
        Remove-Item -LiteralPath $reviewContainer -Recurse -Force
    }
}

$blocking = @($allFindings | Where-Object { $_.Severity -eq 'blocking' })
$advisory = @($allFindings | Where-Object { $_.Severity -ne 'blocking' })

Write-Host ""
Write-Host "=== Cross-review result: $Base..$Head ($headSha) ==="
foreach ($r in $reviewers) {
    $count = @($blocking | Where-Object { $_.Reviewer -eq $r }).Count
    Write-Host "$r verdict: $($verdicts[$r]) ($count blocking)"
}
foreach ($f in $blocking) {
    Write-Host ""
    Write-Host "[$($f.Reviewer) BLOCKING] $($f.File):$($f.Line) -- $($f.Summary)"
    Write-Host "  $($f.Detail)"
}
foreach ($f in $advisory) {
    Write-Host ""
    Write-Host "[$($f.Reviewer) advisory] $($f.File):$($f.Line) -- $($f.Summary)"
}

if ($Number) {
    foreach ($r in $reviewers) {
        $rBlocking = @($blocking | Where-Object { $_.Reviewer -eq $r })
        $rAdvisory = @($advisory | Where-Object { $_.Reviewer -eq $r })
        # The model's own verdict is authoritative and can never be silently
        # downgraded to APPROVE by our own counting; either signal blocks.
        $verdict = if ($rBlocking.Count -gt 0 -or $verdicts[$r] -eq 'REQUEST_CHANGES') { 'REQUEST_CHANGES' } else { 'APPROVE' }
        $marker = New-CrossReviewMarker -Reviewer $r -HeadSha $headSha -Verdict $verdict -Blocking $rBlocking.Count

        # A literal backtick immediately before an interpolated $(...) expression
        # in a double-quoted string is itself an escape character in PowerShell
        # (`$ means a literal, non-interpolated dollar sign), so it silently ate
        # both the intended markdown backtick AND the interpolation. Use a
        # variable holding one backtick instead of writing it inline. Found by a
        # prior cross-review of this exact script, which showed the resulting
        # comment would have printed the whole finding object dumped as text.
        $bt = [char]96
        $rEffort = Get-ReviewerEffort -Reviewer $r -Tier $tier
        $effortSuffix = if ($rEffort) { ", $rEffort effort" } else { '' }
        $lines = @(
            $marker, '',
            "### Cross-model review ($r/$(Get-ReviewerModel -Reviewer $r -Tier $tier)$effortSuffix, reviewing $Base..$Head)",
            "Blast radius: **$tier** ($($changedFiles.Count) file(s) changed)", ''
        )
        if ($rBlocking.Count -eq 0 -and $rAdvisory.Count -eq 0) {
            $lines += 'No findings.'
        }
        foreach ($f in $rBlocking) {
            $lines += "- **BLOCKING** $bt$($f.File):$($f.Line)$bt -- $($f.Summary)"
            $lines += "  $($f.Detail)"
        }
        foreach ($f in $rAdvisory) {
            $lines += "- advisory $bt$($f.File):$($f.Line)$bt -- $($f.Summary)"
        }
        $lines += ''
        $lines += if ($verdict -eq 'APPROVE') {
            'Verdict: APPROVE. This satisfies the cross-review merge gate for this head commit.'
        }
        else {
            "Verdict: REQUEST_CHANGES. $($rBlocking.Count) blocking finding(s) must be addressed, then re-run ``scripts/cross-review.ps1 -Number $Number``."
        }
        $body = $lines -join "`n"
        gh pr comment $Number --body $body | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to post the $r cross-review comment to PR #$Number (gh exited $LASTEXITCODE). The verdict above was NOT recorded; merge-pr.ps1 will still see this as unreviewed. Re-run after fixing auth/network."
        }
        Write-Host "Posted $r review to PR #$Number."
    }
}

if ($blocking.Count -gt 0) {
    Write-Host ""
    Write-Host "BLOCKED: $($blocking.Count) unresolved blocking finding(s). Fix them, or provide -Rebuttal and re-run, before merge-pr.ps1 will allow a merge."
    exit 1
}
Write-Host ""
Write-Host "CLEAN: no blocking findings from $($reviewers -join ', ')."
exit 0
