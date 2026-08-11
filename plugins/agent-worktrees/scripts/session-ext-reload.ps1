# session-ext-reload -- sessionStart hook (hooks.json).
#
# TEMPORARY: emits the extension-reload "Loading…/Resuming…" hang warning
# (github/copilot-agent-runtime#13492; fix: #13494) as
# {"additionalContext": "..."} from the deployed
# ~/.agent-worktrees/bin/ext-reload-hang.md.
#
# Unlike session-conduct this is NOT strictly cwd-gated: it also fires when the
# session cwd is the HOME dir, so it still reaches a **Bare resume** session
# (cwd=~/) -- the exact scenario this warning covers, which a pure get-project
# gate would miss (that is why this warning stayed on the file mechanism until
# now). It stays quiet in unrelated repos (cwd neither a managed project nor
# home). Retired outright -- this script, its hooks.json entry, the fragment, and
# its installer copy -- once the #13494 fix ships everywhere (dotfiles#1055).
#
# Compatible with PowerShell 5.1+ and pwsh 7+.

$ErrorActionPreference = 'SilentlyContinue'

function Emit-Empty {
    Write-Output '{}'
    exit 0
}

$warn = Join-Path $env:USERPROFILE '.agent-worktrees\bin\ext-reload-hang.md'
if (-not (Test-Path -LiteralPath $warn)) { Emit-Empty }

# --- gate: managed project OR cwd == home (Bare resume) ---
$homeDir = $env:USERPROFILE.TrimEnd('\', '/')
$cwd = (Get-Location).Path.TrimEnd('\', '/')
if ($cwd -ine $homeDir) {
    $_r = Join-Path $env:USERPROFILE '.agent-worktrees\bin\resolve-runtime.ps1'
    $python = if (Test-Path -LiteralPath $_r) { . $_r; $AwPy } else { $null }
    if (-not $python) { Emit-Empty }
    $env:PYTHONPATH = ''
    $project = (& $python -m agent_worktrees get project 2>$null | Select-Object -First 1)
    if (-not $project) { Emit-Empty }
}

$t = (Get-Content -Raw -LiteralPath $warn)
if (-not $t) { Emit-Empty }
Write-Output (@{ additionalContext = $t.TrimEnd() } | ConvertTo-Json -Compress -Depth 3)
exit 0
