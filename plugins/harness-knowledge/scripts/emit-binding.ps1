# emit-binding -- harness-knowledge sessionStart hook.
#
# Emits the machine-local knowledge-binding fragment (~/.<harness>/knowledge-binding.md)
# as {"additionalContext": "..."}, cwd-gated to the harness project. This is the
# declarative, launch-path-independent replacement for the per-project
# knowledge-binding.instructions.md that was auto-loaded via
# COPILOT_CUSTOM_INSTRUCTIONS_DIRS (dotfiles#1057 / effort instructions-to-hooks).
#
# The harness (project) is resolved via agent-worktrees; outside a managed
# project, or when no binding exists, it emits {} so nothing leaks into unrelated
# repos. Compatible with PowerShell 5.1+ and pwsh 7+.

$ErrorActionPreference = 'SilentlyContinue'

function Emit-Empty { Write-Output '{}'; exit 0 }

$python = "$env:USERPROFILE\.agent-worktrees\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { Emit-Empty }
$env:PYTHONPATH = ''
$project = (& $python -m agent_worktrees get project 2>$null | Select-Object -First 1)
if (-not $project) { Emit-Empty }

$frag = Join-Path $env:USERPROFILE ".$project\knowledge-binding.md"
if (-not (Test-Path $frag)) { Emit-Empty }

$text = (Get-Content -Raw -LiteralPath $frag)
if (-not $text) { Emit-Empty }

# Drop the ownership-marker line; emit the rest as additionalContext.
$body = (($text -split "`n") | Where-Object { $_.Trim() -ne '<!-- managed by harness-knowledge -->' }) -join "`n"
$body = $body.Trim()
if (-not $body) { Emit-Empty }

Write-Output (@{ additionalContext = $body } | ConvertTo-Json -Compress -Depth 3)
exit 0
