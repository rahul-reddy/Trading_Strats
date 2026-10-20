if ($args.Count -ne 2) { exit 64 }
$command = $args[0]
try {
    $argumentJson = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($args[1]))
    [string[]]$commandArguments = ConvertFrom-Json $argumentJson -NoEnumerate
    for ($index = 0; $index -lt $commandArguments.Count; $index++) {
        # PowerShell deliberately uses legacy native-argument passing for .cmd
        # targets on Windows, where a raw empty string is dropped. Explicit
        # quotes make cmd.exe preserve that argument as empty.
        if ($commandArguments[$index] -eq '') { $commandArguments[$index] = '""' }
    }
}
catch { exit 64 }

& $command @commandArguments
if ($null -eq $LASTEXITCODE) {
    if ($?) { exit 0 }
    exit 1
}
exit $LASTEXITCODE
