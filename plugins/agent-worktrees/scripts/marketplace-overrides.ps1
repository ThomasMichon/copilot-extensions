# Reconcile registered local marketplace sources on session start.

$ErrorActionPreference = 'SilentlyContinue'

$_r = Join-Path $env:USERPROFILE '.agent-worktrees\bin\resolve-runtime.ps1'
$python = if (Test-Path -LiteralPath $_r) { . $_r; $AwPy } else { $null }
if (-not $python) { Write-Output '{}'; exit 0 }

$payload = ''
if ([Console]::IsInputRedirected) {
    try { $payload = [Console]::In.ReadToEnd() } catch { }
}

$env:PYTHONPATH = ''
try {
    $payload | & $python -m agent_worktrees reconcile-marketplaces `
        --stdin --session-start 2>$null
} catch {
    Write-Output '{}'
}
exit 0
