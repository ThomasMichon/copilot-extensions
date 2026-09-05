param(
    [switch]$Recovery
)

if ($Recovery -or $env:AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED -eq '1') {
    return
}

$agentMachines = Get-Command 'agent-machines' -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $agentMachines) {
    $env:AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED = '1'
    return
}

$output = & $agentMachines `
    'restore' `
    '--all-projects' `
    '--only' 'copilot.settings' `
    '--apply' `
    '--json' 2>&1
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    [Console]::Error.WriteLine(
        'ERROR: agent-machines failed to reconcile Copilot settings before launch.'
    )
    if ($output) {
        [Console]::Error.WriteLine(($output | Out-String).TrimEnd())
    }
    exit $exitCode
}

$env:AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED = '1'
