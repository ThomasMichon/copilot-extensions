# emit-codespace-map -- agent-codespaces sessionStart hook (PowerShell).
#
# Emits {"additionalContext": "<map of CodeSpace-delegated repos>"} so a session
# knows which repos have no local checkout and must be worked via a CodeSpace.
# Derived from `agent-worktrees related list --json` (delegate=agent-codespaces),
# cwd-gated to a managed project; emits {} otherwise. See emit_codespace_map.py.
# Compatible with PowerShell 5.1+ and pwsh 7+.

$ErrorActionPreference = 'SilentlyContinue'

function Emit-Empty { Write-Output '{}'; exit 0 }

$_root = Join-Path $env:USERPROFILE '.agent-codespaces'
$_ver = ''
try { $_ver = ([IO.File]::ReadAllText((Join-Path $_root 'current-version'))).Trim() } catch {}
$python = if ($_ver) { Join-Path $_root ("versions\$_ver\Scripts\python.exe") } else { '' }
if (-not ($python -and (Test-Path -LiteralPath $python))) { Emit-Empty }

$script = Join-Path $PSScriptRoot 'emit_codespace_map.py'
if (-not (Test-Path $script)) { Emit-Empty }

$env:PYTHONPATH = ''
$out = (& $python $script @args 2>$null | Select-Object -First 1)
if (-not $out) { Emit-Empty }
Write-Output $out
exit 0
