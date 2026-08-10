# Canonical agent-worktrees runtime resolver -- dot-sourced by the Windows hooks
# and binstubs. Sets $AwPy to the runtime slot python resolved SOLELY via the
# junction-free `current-version` marker (the single source of truth; #1106):
#
#   %USERPROFILE%\.agent-worktrees\current-version -> versions\<ver>\Scripts\python.exe
#
# Nothing resolves through the retired `.venv` junction (a reparse point blocked
# by RedirectionGuard, WinError 448/3, and prone to drift). $AwPy is $null when
# no runtime slot is installed (callers degrade gracefully / no-op).
# Compatible with PowerShell 5.1+ and pwsh 7+.
$AwPy = $null
$_awr = Join-Path $env:USERPROFILE '.agent-worktrees'
$_awv = ''
try { $_awv = ([IO.File]::ReadAllText((Join-Path $_awr 'current-version'))).Trim() } catch {}
if ($_awv) {
  $_p = Join-Path $_awr ("versions\$_awv\Scripts\python.exe")
  if (Test-Path -LiteralPath $_p) { $AwPy = $_p }
}
if (-not $AwPy) {
  # Marker absent/stale -> newest installed slot (best-effort belt).
  $AwPy = Get-ChildItem (Join-Path $_awr 'versions') -Directory -ErrorAction SilentlyContinue |
    Sort-Object Name |
    ForEach-Object { Join-Path $_.FullName 'Scripts\python.exe' } |
    Where-Object { Test-Path -LiteralPath $_ } |
    Select-Object -Last 1
}
