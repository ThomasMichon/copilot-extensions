# Bootstrap check — read-only, runs on session start
# Checks whether the worktree-manager runtime is installed and on PATH.
# If missing, prints a hint. Never installs anything automatically.

$wmPath = Get-Command worktree-manager -ErrorAction SilentlyContinue
if (-not $wmPath) {
    Write-Host ""
    Write-Host "[worktree-manager] Runtime not installed." -ForegroundColor Yellow
    Write-Host "  Run the 'worktree-setup' skill to bootstrap: ask Copilot to 'set up worktree-manager'" -ForegroundColor DarkGray
    Write-Host ""
}
exit 0
