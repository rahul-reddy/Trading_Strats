$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/lib/cross-review.ps1"

function Assert-Equal {
    param([object]$Actual, [object]$Expected, [string]$Label)
    if ($Actual -ne $Expected) {
        throw "$Label`: expected '$Expected', got '$Actual'"
    }
}

function Review-Comment {
    param(
        [string]$Reviewer,
        [string]$Head,
        [string]$Verdict = 'APPROVE',
        [int]$Blocking = 0,
        [datetime]$CreatedAt = [datetime]::UtcNow
    )
    [pscustomobject]@{
        body = New-CrossReviewMarker -Reviewer $Reviewer -HeadSha $Head `
            -Verdict $Verdict -Blocking $Blocking
        createdAt = $CreatedAt.ToString('o')
    }
}

$head = '0123456789abcdef0123456789abcdef01234567'
$oldHead = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'

Assert-Equal (Get-BlastRadiusTier -ChangedFiles @('docs/guide.md')) 'low' 'docs tier'
Assert-Equal (Get-BlastRadiusTier -ChangedFiles @('tqqq-blend/README.md')) 'low' 'strategy readme tier'
Assert-Equal (Get-BlastRadiusTier -ChangedFiles @('tqqq-blend/tqqq_blend/runner.py')) 'critical' 'runner tier'
Assert-Equal (Get-BlastRadiusTier -ChangedFiles @('tqqq-blend/tqqq_blend/brokers/ibkr.py')) 'critical' 'broker tier'
Assert-Equal (Get-BlastRadiusTier -ChangedFiles @('tqqq-blend/config.ibkr-live.toml')) 'critical' 'live config tier'
Assert-Equal (Get-BlastRadiusTier -ChangedFiles @('tqqq-blend/deploy/server/install.sh')) 'critical' 'deploy tier'
Assert-Equal (Get-BlastRadiusTier -ChangedFiles @('scripts/merge-pr.ps1')) 'critical' 'gate tier'
Assert-Equal (Get-BlastRadiusTier -ChangedFiles @('.github/workflows/ci.yml')) 'critical' 'ci tier'
Assert-Equal (Get-BlastRadiusTier -ChangedFiles @('tqqq-blend/tqqq_blend/backtest.py')) 'standard' 'backtest tier'
Assert-Equal (Get-BlastRadiusTier -ChangedFiles @('tqqq-blend/tests/test_strategy.py') `
    -Deletions @{ 'tqqq-blend/tests/test_strategy.py' = 1 }) 'standard' 'test deletion tier'
Assert-Equal (Get-BlastRadiusTier -ChangedFiles @('tqqq-blend/tests/test_strategy.py') `
    -Deletions @{ 'tqqq-blend/tests/test_strategy.py' = 0 }) 'low' 'additive test tier'
Assert-Equal (Get-BlastRadiusTier -ChangedFiles @('tqqq-blend/tests/test_strategy.py')) 'standard' `
    'test with unconfirmed deletion count'
Assert-Equal (@(Get-AuthorModelsFromMessages -CommitMessages `
    "change`n`nCo-Authored-By: Codex <noreply@openai.com>")[0]) 'codex' 'author detection'

Assert-Equal (Test-CrossReviewSatisfied -Authors @() -Comments @() -HeadSha $head) $true `
    'human-authored change'
Assert-Equal (Test-CrossReviewSatisfied -Authors @('codex') `
    -Comments @(Review-Comment -Reviewer gemini -Head $head) -HeadSha $head) $true `
    'eligible same-head approval'
Assert-Equal (Test-CrossReviewSatisfied -Authors @('codex') `
    -Comments @(Review-Comment -Reviewer gemini -Head $oldHead) -HeadSha $head) $false `
    'stale approval'
Assert-Equal (Test-CrossReviewSatisfied -Authors @('codex') -Comments @(
        Review-Comment -Reviewer gemini -Head $head -CreatedAt ([datetime]::UtcNow.AddMinutes(-1))
        Review-Comment -Reviewer claude -Head $head -Verdict REQUEST_CHANGES -Blocking 1
    ) -HeadSha $head) $false 'one rejection blocks another approval'
Assert-Equal (Test-CrossReviewSatisfied -Authors @('codex') `
    -Comments @(Review-Comment -Reviewer gemini -Head $head) -HeadSha $head -Tier critical) $false `
    'gemini cannot approve critical change'

# Grok is the last-resort backup: after every other reviewer, still excluded
# from reviewing its own work, and escalatable, so eligible on critical diffs.
Assert-Equal ((Get-EligibleReviewers -Authors @('claude')) -join ',') 'gemini,codex,grok' `
    'grok is the last fallback'
Assert-Equal ((Get-EligibleReviewers -Authors @('claude') -Tier critical) -join ',') 'codex,grok' `
    'grok may decide critical diffs'
Assert-Equal ((Get-EligibleReviewers -Authors @('grok')) -join ',') 'gemini,codex,claude' `
    'grok never reviews its own commits'
Assert-Equal (@(Get-AuthorModelsFromMessages -CommitMessages `
    "change`n`nCo-Authored-By: Grok 4.7 <noreply@x.ai>")[0]) 'grok' 'grok author detection'
Assert-Equal (Get-ReviewerModel -Reviewer grok -Tier critical) 'grok-4.7' 'grok pinned model'
Assert-Equal (Test-CrossReviewSatisfied -Authors @('claude') `
    -Comments @(Review-Comment -Reviewer grok -Head $head) -HeadSha $head -Tier critical) $true `
    'grok approval satisfies the gate'
Assert-Equal (Test-CrossReviewSatisfied -Authors @('claude') -Comments @(
        Review-Comment -Reviewer grok -Head $head -CreatedAt ([datetime]::UtcNow.AddMinutes(-1))
        Review-Comment -Reviewer codex -Head $head -Verdict REQUEST_CHANGES -Blocking 1
    ) -HeadSha $head) $false 'grok approval cannot override a rejection'

$keyRoot = Join-Path ([IO.Path]::GetTempPath()) ("xai-key-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $keyRoot | Out-Null
$savedKey = $env:XAI_API_KEY
try {
    $env:XAI_API_KEY = $null
    Set-Content -Path (Join-Path $keyRoot '.env') -Value @('OTHER=1', 'XAI_API_KEY="test-key"')
    Assert-Equal (Get-XaiApiKey -RepositoryRoot $keyRoot) 'test-key' 'xai key from .env'
    Remove-Item -Force (Join-Path $keyRoot '.env')
    Assert-Equal (Get-XaiApiKey -RepositoryRoot $keyRoot) $null 'missing xai key'
}
finally {
    $env:XAI_API_KEY = $savedKey
    Remove-Item -LiteralPath $keyRoot -Recurse -Force -ErrorAction SilentlyContinue
}

# Streamed xAI parsing, against a local server: a complete stream, one cut off
# before [DONE], an HTTP error, an error body and a stream that each stall past
# the deadline, and an answer from a model other than the pinned one.
$port = Get-Random -Minimum 20000 -Maximum 60000
$server = Start-ThreadJob -ArgumentList $port -ScriptBlock {
    param($port)
    $listener = [Net.HttpListener]::new()
    $listener.Prefixes.Add("http://127.0.0.1:$port/")
    $listener.Start()
    try {
        for ($i = 0; $i -lt 6; $i++) {
            $context = $listener.GetContext()
            $path = $context.Request.Url.AbsolutePath
            $out = $context.Response
            # The stall cases outlive their client, whose disconnect makes the
            # late write throw; one aborted exchange must not stop the server.
            try {
                if ($path -in @('/error', '/error-stall')) {
                    $out.StatusCode = 429
                    $out.SendChunked = $true
                    $bytes = [Text.Encoding]::UTF8.GetBytes('{"error":"quota"')
                    $out.OutputStream.Write($bytes, 0, $bytes.Length)
                    $out.OutputStream.Flush()
                    if ($path -eq '/error-stall') { Start-Sleep -Seconds 4 }
                    $bytes = [Text.Encoding]::UTF8.GetBytes('}')
                    $out.OutputStream.Write($bytes, 0, $bytes.Length)
                    $out.Close()
                    continue
                }
                $out.ContentType = 'text/event-stream'
                $out.SendChunked = $true
                $lines = @(
                    'data: {"model":"grok-4.7","choices":[{"delta":{"content":"{\"verdict\":"}}]}',
                    '',
                    'data: {"model":"grok-4.7","choices":[{"delta":{"content":"\"APPROVE\"}"}}]}',
                    ''
                )
                if ($path -eq '/complete') { $lines += @('data: [DONE]', '') }
                $bytes = [Text.Encoding]::UTF8.GetBytes(($lines -join "`n") + "`n")
                $out.OutputStream.Write($bytes, 0, $bytes.Length)
                $out.OutputStream.Flush()
                if ($path -eq '/stall') { Start-Sleep -Seconds 4 }
                $out.Close()
            }
            catch { }
        }
    }
    finally { $listener.Stop() }
}
try {
    $request = [ordered]@{ model = 'grok-4.7'; messages = @(@{ role = 'user'; content = 'x' }) }
    $deadline = [datetime]::UtcNow.AddSeconds(10)
    $streamed = $null
    while (-not $streamed -and [datetime]::UtcNow -lt $deadline) {
        try {
            $streamed = Invoke-XaiChatStream -ApiKey 'k' -Request $request -TimeoutSeconds 10 `
                -ExpectedModel 'grok-4.7' -Uri "http://127.0.0.1:$port/complete"
        }
        catch { Start-Sleep -Milliseconds 200 }
    }
    Assert-Equal $streamed.Model 'grok-4.7' 'streamed model'
    Assert-Equal $streamed.Content '{"verdict":"APPROVE"}' 'streamed content'
    foreach ($case in @(
            @{ Path = 'truncated'; Pattern = 'ended before completion'; Timeout = 10 },
            @{ Path = 'error'; Pattern = 'HTTP 429'; Timeout = 10 },
            @{ Path = 'stall'; Pattern = 'exceeded the 1-second timeout'; Timeout = 1 },
            @{ Path = 'error-stall'; Pattern = 'exceeded the 1-second timeout'; Timeout = 1 },
            @{ Path = 'complete'; Pattern = "answered as 'grok-4.7', not the pinned 'grok-9'"
                Timeout = 10; Expected = 'grok-9' })) {
        $message = ''
        try {
            $expected = if ($case.Expected) { $case.Expected } else { 'grok-4.7' }
            Invoke-XaiChatStream -ApiKey 'k' -Request $request -TimeoutSeconds $case.Timeout `
                -ExpectedModel $expected -Uri "http://127.0.0.1:$port/$($case.Path)" | Out-Null
        }
        catch { $message = $_.Exception.Message }
        if ($message -notmatch $case.Pattern) {
            throw "xai stream '$($case.Path)': expected /$($case.Pattern)/, got '$message'"
        }
    }
}
finally {
    $server | Wait-Job -Timeout 10 | Out-Null
    $server | Remove-Job -Force
}

$bounded = Invoke-BoundedProcess -FilePath (Get-Process -Id $PID).Path `
    -ArgumentList @('-NoProfile', '-Command', '$input | Out-String') `
    -WorkingDirectory $PSScriptRoot -StandardInput 'bounded-process-ok' -TimeoutSeconds 10
Assert-Equal $bounded.ExitCode 0 'bounded process exit'
if ($bounded.Stdout -notmatch 'bounded-process-ok') { throw 'bounded process lost standard input' }

$argumentTemp = New-TemporaryFile
$argumentProbe = [IO.Path]::ChangeExtension($argumentTemp.FullName, '.ps1')
Move-Item -LiteralPath $argumentTemp -Destination $argumentProbe
try {
    Set-Content -Path $argumentProbe -Value 'Write-Output ($args | ConvertTo-Json -Compress)'
    $argumentResult = Invoke-BoundedProcess -FilePath $argumentProbe `
        -ArgumentList @('before', '', 'after') -WorkingDirectory $PSScriptRoot `
        -StandardInput '' -TimeoutSeconds 10
    $roundTripped = @($argumentResult.Stdout | ConvertFrom-Json)
    Assert-Equal $roundTripped.Count 3 'external script argument count'
    Assert-Equal $roundTripped[1] '' 'external script empty argument'
}
finally {
    Remove-Item -LiteralPath $argumentProbe -ErrorAction SilentlyContinue
}

if ($IsWindows) {
    $commandTemp = New-TemporaryFile
    $commandProbe = [IO.Path]::ChangeExtension($commandTemp.FullName, '.cmd')
    $commandTarget = [IO.Path]::ChangeExtension($commandTemp.FullName, '.target.ps1')
    Move-Item -LiteralPath $commandTemp -Destination $commandProbe
    try {
        Set-Content -Path $commandTarget -Value 'Write-Output ($args | ConvertTo-Json -Compress)'
        Set-Content -Path $commandProbe -Value "@pwsh -NoProfile -File `"$commandTarget`" %*"
        $commandResult = Invoke-BoundedProcess -FilePath $commandProbe `
            -ArgumentList @('-p', '', 'tail') `
            -WorkingDirectory $PSScriptRoot -StandardInput '' -TimeoutSeconds 10
        $commandArguments = @($commandResult.Stdout | ConvertFrom-Json)
        Assert-Equal $commandArguments.Count 3 'Windows command-shim argument count'
        Assert-Equal $commandArguments[1] '' 'Windows command-shim empty argument'
    }
    finally {
        Remove-Item -LiteralPath $commandProbe -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $commandTarget -ErrorAction SilentlyContinue
    }
}

$earlyExit = Invoke-BoundedProcess -FilePath (Get-Process -Id $PID).Path `
    -ArgumentList @('-NoProfile', '-Command', 'exit 7') -WorkingDirectory $PSScriptRoot `
    -StandardInput ('x' * 2MB) -TimeoutSeconds 10
Assert-Equal $earlyExit.ExitCode 7 'early exit while receiving input'

$unicode = Invoke-BoundedProcess -FilePath (Get-Process -Id $PID).Path `
    -ArgumentList @('-NoProfile', '-Command', '$input | Out-String') `
    -WorkingDirectory $PSScriptRoot -StandardInput 'café — exact' -TimeoutSeconds 10
if ($unicode.Stdout -notmatch 'café — exact') { throw 'bounded process did not preserve UTF-8 text' }

$unsupportedCommandFailedClearly = $false
try {
    Invoke-BoundedProcess -FilePath Get-ChildItem -ArgumentList @() `
        -WorkingDirectory $PSScriptRoot -StandardInput '' -TimeoutSeconds 10 | Out-Null
}
catch {
    $unsupportedCommandFailedClearly = $_.Exception.Message -match 'application or script file'
}
Assert-Equal $unsupportedCommandFailedClearly $true 'unsupported command error'

$timedOut = $false
try {
    Invoke-BoundedProcess -FilePath (Get-Process -Id $PID).Path `
        -ArgumentList @('-NoProfile', '-Command', 'Start-Sleep -Seconds 5') `
        -WorkingDirectory $PSScriptRoot -StandardInput '' -TimeoutSeconds 1 | Out-Null
}
catch {
    $timedOut = $_.Exception.Message -match 'exceeded the 1-second timeout'
}
Assert-Equal $timedOut $true 'bounded process timeout'

# Reviewers run with no MCP servers (see Get-ReviewerNoMcpArguments).
Assert-Equal ((Get-ReviewerNoMcpArguments -Reviewer claude) -join ' ') '--strict-mcp-config' `
    'claude loads no MCP servers'
Assert-Equal @(Get-ReviewerNoMcpArguments -Reviewer gemini).Count 0 `
    'gemini (agy) takes no MCP flag; Assert-AgyHasNoExtensions checks instead'

# Gemini runs through the Antigravity CLI (agy): stdin message shape, and a
# stream that fails closed on a model swap, an ERROR status or a missing event.
$agyInput = (New-AgyStreamInput -Prompt "review `"this`"`nline 2") | ConvertFrom-Json
Assert-Equal "$($agyInput.event)|$($agyInput.message.content)" "user|review `"this`"`nline 2" 'agy stdin message'
$agyInit = '{"event":"init","init":{"model":"gemini-3.8-flash-high","cwd":"x"}}'
$agyStep = '{"event":"step_update","step_update":{"state":"DONE"}}'
$agyOk = '{"event":"result","result":{"status":"SUCCESS","response":"{\"verdict\":\"APPROVE\"}"}}'
Assert-Equal (Get-AgyStreamResult -Stdout "noise`n$agyInit`n$agyStep`n$agyOk`n" -ExpectedModel 'gemini-3.8-flash-high') `
    '{"verdict":"APPROVE"}' 'agy result response'
$agyError = '{"event":"result","result":{"status":"ERROR","response":"","error":"empty prompt"}}'
$agyCases = [ordered]@{
    'model swap'   = @("$agyInit`n$agyOk", 'gemini-3.8-flash-low', 'not the pinned')
    'error status' = @("$agyInit`n$agyError", 'gemini-3.8-flash-high', "status 'ERROR'")
    'no init'      = @($agyOk, 'gemini-3.8-flash-high', 'no init event')
    'no result'    = @($agyInit, 'gemini-3.8-flash-high', 'no result event')
}
foreach ($case in $agyCases.GetEnumerator()) {
    $message = ''
    try { Get-AgyStreamResult -Stdout $case.Value[0] -ExpectedModel $case.Value[1] | Out-Null }
    catch { $message = $_.Exception.Message }
    Assert-Equal ($message -match [regex]::Escape($case.Value[2])) $true "agy fails closed: $($case.Key)"
}
Assert-Equal (@(Get-AuthorModelsFromMessages -CommitMessages `
    "change`n`nCo-Authored-By: Antigravity <noreply@google.com>")[0]) 'gemini' 'antigravity author is gemini'
Assert-Equal @(Get-ReviewerNoMcpArguments -Reviewer grok).Count 0 'grok has no MCP servers'
Assert-Equal @(Get-ReviewerNoMcpArguments -Reviewer codex).Count 0 'codex with nothing configured'
Assert-Equal ((Get-ReviewerNoMcpArguments -Reviewer codex -CodexServerNames @('rea', 'my_tool-2')) -join ' ') `
    '-c mcp_servers.rea.enabled=false -c mcp_servers.my_tool-2.enabled=false' `
    'codex disables every configured server by name'
foreach ($unsafeName in @('a.b', 'a"b', '')) {
    $unsafeRefused = $false
    try { Get-CodexMcpDisableArguments -ServerNames @($unsafeName) | Out-Null }
    catch { $unsafeRefused = $true }
    Assert-Equal $unsafeRefused $true "codex refuses MCP server name '$unsafeName'"
}

$fakeCodexTemp = New-TemporaryFile
$fakeCodex = [IO.Path]::ChangeExtension($fakeCodexTemp.FullName, '.ps1')
Move-Item -LiteralPath $fakeCodexTemp -Destination $fakeCodex
try {
    Set-Content -Path $fakeCodex -Value @(
        'if (($args -join " ") -ne "mcp list --json") { exit 9 }'
        'Write-Output ''[{"name":"rea","enabled":true}]'''
    )
    Assert-Equal ((Get-CodexMcpServerNames -WorkingDirectory $PSScriptRoot -CodexCommand $fakeCodex) -join ',') `
        'rea' 'codex MCP server names from a single-server listing'
    Set-Content -Path $fakeCodex -Value 'Write-Output ''[{"name":"rea"},{"name":"other"}]'''
    Assert-Equal ((Get-CodexMcpServerNames -WorkingDirectory $PSScriptRoot -CodexCommand $fakeCodex) -join ',') `
        'rea,other' 'codex MCP server names from a multi-server listing'
    Set-Content -Path $fakeCodex -Value 'Write-Output ''[]'''
    Assert-Equal @(Get-CodexMcpServerNames -WorkingDirectory $PSScriptRoot -CodexCommand $fakeCodex).Count 0 `
        'codex MCP server names from an empty listing'
    # Composed exactly as Invoke-Reviewer does, so an empty listing reaching
    # Get-ReviewerNoMcpArguments as $null would fail here, not in a live review.
    $codexServers = @(Get-CodexMcpServerNames -WorkingDirectory $PSScriptRoot -CodexCommand $fakeCodex)
    $codexArguments = @('exec')
    $codexArguments += @(Get-ReviewerNoMcpArguments -Reviewer codex -CodexServerNames $codexServers)
    Assert-Equal ($codexArguments -join ' ') 'exec' 'codex invocation with an empty MCP listing'
    foreach ($badListing in @('exit 3', 'Write-Output "not json"', 'Write-Output ''[{"enabled":true}]''')) {
        Set-Content -Path $fakeCodex -Value $badListing
        $listingRefused = $false
        try { Get-CodexMcpServerNames -WorkingDirectory $PSScriptRoot -CodexCommand $fakeCodex | Out-Null }
        catch { $listingRefused = $true }
        Assert-Equal $listingRefused $true "codex MCP listing failure fails closed ($badListing)"
    }
}
finally {
    Remove-Item -LiteralPath $fakeCodex -ErrorAction SilentlyContinue
}

Write-Host 'Cross-review self-test passed.'
