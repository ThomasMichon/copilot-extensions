# session-conduct -- sessionStart hook (hooks.json).
#
# Emits the static "conduct" guidance fragments deployed under
# ~/.agent-worktrees/bin/conduct/*.md as {"additionalContext": "..."} -- but
# ONLY when the session's cwd is inside an agent-worktrees-managed project.
# Outside a managed project it emits {} so a globally-loaded plugin never leaks
# guidance into unrelated repos (cwd self-gating).
#
# This is the declarative, launch-path-independent replacement for the
# per-project *.instructions.md files formerly deployed into
# ~/.{project}/.github/instructions/ and loaded via
# COPILOT_CUSTOM_INSTRUCTIONS_DIRS (dotfiles#1053 / effort instructions-to-hooks).
# Because it runs at sessionStart it fires on new AND resumed sessions and under
# any launch path, not just launcher-wrapped ones.
#
# Compatible with PowerShell 5.1+ and pwsh 7+.

$ErrorActionPreference = 'SilentlyContinue'

function Emit-Empty {
    Write-Output '{}'
    exit 0
}

# --- cwd gate: only inside an agent-worktrees-managed project ---
$python = "$env:USERPROFILE\.agent-worktrees\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { Emit-Empty }
$env:PYTHONPATH = ''
$project = (& $python -m agent_worktrees get project 2>$null | Select-Object -First 1)
if (-not $project) { Emit-Empty }

# --- collect deployed conduct fragments ---
$dir = Join-Path $env:USERPROFILE '.agent-worktrees\bin\conduct'
if (-not (Test-Path $dir)) { Emit-Empty }

$parts = @()
foreach ($f in (Get-ChildItem -Path $dir -Filter '*.md' -File -ErrorAction SilentlyContinue | Sort-Object Name)) {
    $t = (Get-Content -Raw -LiteralPath $f.FullName)
    if ($t) { $parts += $t.TrimEnd() }
}
if ($parts.Count -eq 0) { Emit-Empty }

$ctx = ($parts -join "`n`n")
Write-Output (@{ additionalContext = $ctx } | ConvertTo-Json -Compress -Depth 3)
exit 0
