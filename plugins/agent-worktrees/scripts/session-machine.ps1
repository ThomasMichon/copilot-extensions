# session-machine -- sessionStart hook (hooks.json).
#
# Emits the machine-identity block as {"additionalContext": "..."}, computed live
# from machines.yaml by `agent_worktrees machine-context`. The Python command is
# cwd-gated (emits {} outside an agent-worktrees-managed project), so a
# globally-loaded plugin never leaks machine identity into unrelated repos.
#
# Declarative, launch-path-independent replacement for the per-project
# machine.instructions.md + nested AGENTS.md that were loaded via
# COPILOT_CUSTOM_INSTRUCTIONS_DIRS (dotfiles#1056 / effort instructions-to-hooks).
# Compatible with PowerShell 5.1+ and pwsh 7+.

$ErrorActionPreference = 'SilentlyContinue'

$_r = Join-Path $env:USERPROFILE '.agent-worktrees\bin\resolve-runtime.ps1'
$python = if (Test-Path -LiteralPath $_r) { . $_r; $AwPy } else { $null }
if (-not $python) { Write-Output '{}'; exit 0 }
$env:PYTHONPATH = ''
$out = (& $python -m agent_worktrees machine-context 2>$null | Select-Object -First 1)
if ($out) { Write-Output $out } else { Write-Output '{}' }
exit 0
