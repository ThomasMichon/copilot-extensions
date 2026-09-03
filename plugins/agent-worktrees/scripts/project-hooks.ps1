# Project hooks runner -- runs on session start via hooks.json
# Discovers and executes per-project session-start hooks from the
# project config directory (~/.{project}/hooks/session-start.ps1).
# Compatible with PowerShell 5.1+ and pwsh 7+.

$ErrorActionPreference = 'SilentlyContinue'

$payload = ''
if ([Console]::IsInputRedirected) {
    try { $payload = [Console]::In.ReadToEnd() } catch { }
}

# Prefer the resident monitor's warm project resolution. The deployed CLI
# remains the bounded fallback when the monitor is unavailable.
$_r = Join-Path $env:USERPROFILE '.agent-worktrees\bin\resolve-runtime.ps1'
$python = if (Test-Path -LiteralPath $_r) { . $_r; $AwPy } else { $null }
if (-not $python) { exit 0 }
$env:PYTHONPATH = ''
$HookPath = ''
$resolvedByMonitor = $false
$client = Join-Path $env:USERPROFILE '.agent-worktrees\bin\hook_client.py'
if (Test-Path -LiteralPath $client -PathType Leaf) {
    try {
        $lines = @(
            $payload | & $python $client projectResolve 2>$null |
                ForEach-Object { [string]$_ }
        )
        if ($lines.Count -ge 2 -and $lines[-1] -eq '0') {
            $resolvedByMonitor = $true
            if ($lines[0] -ne '-') {
                $HookPath = [string]$lines[0]
            }
        }
    } catch { }
}
if ($resolvedByMonitor -and -not $HookPath) { exit 0 }
if (-not $resolvedByMonitor) {
    $ProjectName = (
        & $python -m agent_worktrees get project 2>$null |
            Select-Object -First 1
    )
    if (-not $ProjectName) { exit 0 }
    $HookPath = Join-Path $env:USERPROFILE ".$ProjectName\hooks\session-start.ps1"
}
if (-not (Test-Path $HookPath)) { exit 0 }

try {
    if ($payload) {
        $payload | & $HookPath
    } else {
        & $HookPath
    }
} catch { }

exit 0
