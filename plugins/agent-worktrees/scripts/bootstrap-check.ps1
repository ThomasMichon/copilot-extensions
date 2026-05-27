# Bootstrap check — read-only, runs on session start
# Checks whether the agent-worktrees runtime is installed and on PATH.
# If missing, prints a hint. Never installs anything automatically.

$wmPath = Get-Command agent-worktrees -ErrorAction SilentlyContinue
if (-not $wmPath) {
    Write-Host ""
    Write-Host "[agent-worktrees] Runtime not installed." -ForegroundColor Yellow
    Write-Host "  Run the 'worktree-setup' skill to bootstrap: ask Copilot to 'set up agent-worktrees'" -ForegroundColor DarkGray
    Write-Host ""
}
exit 0
