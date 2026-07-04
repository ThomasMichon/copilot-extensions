# Project hooks runner -- runs on session start via hooks.json
# Discovers and executes per-project session-start hooks from the
# project config directory (~/.{project}/hooks/session-start.ps1).
# Compatible with PowerShell 5.1+ and pwsh 7+.

$ErrorActionPreference = 'SilentlyContinue'

# Resolve the project from CWD (git-like); this hook runs in the worktree.
$python = "$env:USERPROFILE\.agent-worktrees\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { exit 0 }
$env:PYTHONPATH = ''
$ProjectName = (& $python -m agent_worktrees get project 2>$null | Select-Object -First 1)
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
