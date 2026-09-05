$ErrorActionPreference = 'Stop'

$recovery = $false
$offset = 0
if ($args.Count -gt $offset -and $args[$offset] -eq '--recovery') {
    $recovery = $true
    $offset++
}
if ($args.Count -gt $offset -and $args[$offset] -eq '--') {
    $offset++
}
if ($args.Count -le $offset) {
    [Console]::Error.WriteLine('ERROR: launch-command.ps1 requires a command.')
    exit 2
}

$machineSettingsHelper = Join-Path $PSScriptRoot 'reconcile-machine-settings.ps1'
if (Test-Path -LiteralPath $machineSettingsHelper) {
    . $machineSettingsHelper -Recovery:$recovery
}

$executable = [string]$args[$offset]
[string[]]$remainingArgs = @()
if ($args.Count -gt ($offset + 1)) {
    $remainingArgs = $args[($offset + 1)..($args.Count - 1)]
}

$usesDefaultSetup = $false
$executableName = [IO.Path]::GetFileNameWithoutExtension($executable)
if ($executableName -in @('pwsh', 'powershell')) {
    for ($index = 0; $index -lt ($remainingArgs.Count - 1); $index++) {
        if (
            [string]::Equals(
                $remainingArgs[$index],
                '-File',
                [StringComparison]::OrdinalIgnoreCase
            ) -and
            [IO.Path]::GetFileName($remainingArgs[$index + 1]) -eq 'default-setup.ps1'
        ) {
            $usesDefaultSetup = $true
            break
        }
    }
}
if (-not $usesDefaultSetup) {
    Remove-Item Env:AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED -ErrorAction SilentlyContinue
}

& $executable @remainingArgs
exit $LASTEXITCODE
