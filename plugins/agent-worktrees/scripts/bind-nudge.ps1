# bind-nudge -- postToolUse hook (hooks.json). See bind-nudge.sh for the rationale.
#
# Detects an unbound-but-active worktree and emits an additionalContext nudge
# inviting the agent to run `agent-worktrees bind-session --worktree-dir=<dir>`.
# The bind is the agent's explicit act; this hook only detects and prompts.
# Fail-open: emits '{}' on any problem so a nudge never disturbs the tool result.

$ErrorActionPreference = 'SilentlyContinue'

function Emit-Empty { [Console]::Out.Write('{}'); exit 0 }

$_r = Join-Path $env:USERPROFILE '.agent-worktrees\bin\resolve-runtime.ps1'
$python = if (Test-Path -LiteralPath $_r) { . $_r; $AwPy } else { $null }
if (-not $python) { Emit-Empty }

# Read the postToolUse payload from stdin (only when redirected).
$payload = ''
if ([Console]::IsInputRedirected) {
    try { $payload = [Console]::In.ReadToEnd() } catch { }
}

$env:PYTHONPATH = ''  # package is installed in the venv (no lib/ shadow)
try {
    $out = $payload | & $python -m agent_worktrees bind-nudge --stdin 2>$null
    if ($out) { [Console]::Out.Write($out) } else { [Console]::Out.Write('{}') }
} catch { [Console]::Out.Write('{}') }

exit 0
