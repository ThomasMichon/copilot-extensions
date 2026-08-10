$env:PYTHONUTF8 = '1'
# Resolve the runtime slot python SOLELY via the junction-free `current-version`
# marker and launch it directly. The `.venv` junction is retired (marker model,
# #581/#1085/#1106): nothing traverses/parses a reparse point (blocked under
# RedirectionGuard, WinError 448/3, and prone to drift). Fallback: the newest
# versions/ slot only. dotfiles #637 / #1085 / #1106.
$_root = Join-Path $env:USERPROFILE '.agent-worktrees'
$_ver = ''
try { $_ver = ([IO.File]::ReadAllText((Join-Path $_root 'current-version'))).Trim() } catch {}
$_py = if ($_ver) { Join-Path $_root ('versions\' + $_ver + '\Scripts\python.exe') } else { '' }
if (-not ($_py -and (Test-Path -LiteralPath $_py))) { $_py = Get-ChildItem (Join-Path $_root 'versions') -Directory -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object { Join-Path $_.FullName 'Scripts\python.exe' } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -Last 1 }
& $_py -m agent_worktrees @args
exit $LASTEXITCODE
