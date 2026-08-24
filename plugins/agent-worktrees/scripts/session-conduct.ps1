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
$_r = Join-Path $env:USERPROFILE '.agent-worktrees\bin\resolve-runtime.ps1'
$python = if (Test-Path -LiteralPath $_r) { . $_r; $AwPy } else { $null }
if (-not $python) { Emit-Empty }
$env:PYTHONPATH = ''
$project = (& $python -m agent_worktrees get project 2>$null | Select-Object -First 1)
if (-not $project) { Emit-Empty }

# --- collect deployed conduct fragments ---
$parts = @()

# Dynamic: the "the user's state repo" definition (binds the term to the
# resolved checkout so downstream plugins can refer to it in plain prose).
$defn = (& $python -m agent_worktrees state-root --conduct 2>$null | Out-String).Trim()
if ($defn) { $parts += $defn }

# Dynamic: complete related-repo guidance from the merged project corpus.
$related = (& $python -m agent_worktrees related --conduct 2>$null | Out-String).Trim()
if ($related) { $parts += $related }

$dir = Join-Path $env:USERPROFILE '.agent-worktrees\bin\conduct'
if (Test-Path $dir) {
    foreach ($f in (Get-ChildItem -Path $dir -Filter '*.md' -File -ErrorAction SilentlyContinue | Sort-Object Name)) {
        $t = (Get-Content -Raw -LiteralPath $f.FullName)
        if ($t) { $parts += $t.TrimEnd() }
    }
}

# Dynamic: the worktree's own recent-history recovery digest (record-first
# recovery -- what this worktree has been doing, so a fresh/successor session
# inherits it even if a live handoff never completed). Empty when no history.
$digest = (& $python -m agent_worktrees history-digest 2>$null | Out-String).Trim()
if ($digest) { $parts += $digest }

if ($parts.Count -eq 0) { Emit-Empty }

$ctx = ($parts -join "`n`n")
Write-Output (@{ additionalContext = $ctx } | ConvertTo-Json -Compress -Depth 3)
exit 0
