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
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Emit-Empty {
    Write-Output '{}'
    exit 0
}

# --- cwd gate: only inside an agent-worktrees-managed project ---
$_r = Join-Path $env:USERPROFILE '.agent-worktrees\bin\resolve-runtime.ps1'
$python = if (Test-Path -LiteralPath $_r) { . $_r; $AwPy } else { $null }
if (-not $python) { Emit-Empty }
$env:PYTHONPATH = ''
$project = (& $python -m agent_worktrees get project 2>$null | Select-Object -First 1)
if (-not $project) { Emit-Empty }

# --- collect dynamic conduct; one Python assembler owns ordering + budget ---
$defn = (& $python -m agent_worktrees state-root --conduct 2>$null | Out-String).Trim()
$related = (& $python -m agent_worktrees --project $project related --conduct 2>$null | Out-String).Trim()
$dir = Join-Path $env:USERPROFILE '.agent-worktrees\bin\conduct'

# Dynamic: the worktree's own recent-history recovery digest (record-first
# recovery -- what this worktree has been doing, so a fresh/successor session
# inherits it even if a live handoff never completed). Empty when no history.
$digest = (& $python -m agent_worktrees history-digest 2>$null | Out-String).Trim()
$env:AW_CONDUCT_DEFINITION = $defn
$env:AW_CONDUCT_RELATED = $related
$env:AW_CONDUCT_HISTORY = $digest
& $python -m agent_worktrees.conduct $dir
exit 0
