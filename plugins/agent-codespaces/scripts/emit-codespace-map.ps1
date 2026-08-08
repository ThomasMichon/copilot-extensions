# emit-codespace-map -- agent-codespaces sessionStart hook (PowerShell).
#
# Emits {"additionalContext": "<map of CodeSpace-delegated repos>"} so a session
# knows which repos have no local checkout and must be worked via a CodeSpace.
# Derived from `agent-worktrees related list --json` (delegate=agent-codespaces),
# cwd-gated to a managed project; emits {} otherwise. See emit_codespace_map.py.
# Compatible with PowerShell 5.1+ and pwsh 7+.

$ErrorActionPreference = 'SilentlyContinue'

function Emit-Empty { Write-Output '{}'; exit 0 }

$python = "$env:USERPROFILE\.agent-worktrees\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { Emit-Empty }

$script = Join-Path $PSScriptRoot 'emit_codespace_map.py'
if (-not (Test-Path $script)) { Emit-Empty }

$env:PYTHONPATH = ''
$out = (& $python $script 2>$null | Select-Object -First 1)
if (-not $out) { Emit-Empty }
Write-Output $out
exit 0
