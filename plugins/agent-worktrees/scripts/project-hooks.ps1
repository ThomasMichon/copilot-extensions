# Project hooks runner -- runs on session start via hooks.json
# Discovers and executes per-project session-start hooks from the
# project config directory (~/.{project}/hooks/session-start.ps1).
# Compatible with PowerShell 5.1+ and pwsh 7+.

$ErrorActionPreference = 'SilentlyContinue'

$ProjectName = $env:WORKTREE_PROJECT
if (-not $ProjectName) { exit 0 }

$HookPath = Join-Path $env:USERPROFILE ".$ProjectName\hooks\session-start.ps1"
if (-not (Test-Path $HookPath)) { exit 0 }

try {
    $p = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($p) {
        & $p.Source -NoProfile -File $HookPath
    } else {
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File $HookPath
    }
} catch { }

exit 0
