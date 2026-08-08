$env:PYTHONUTF8 = '1'
# Resolve the runtime slot python via the junction-free `current-version` marker
# and launch it directly -- the historical `.venv` junction is retired (marker
# model, #581/#285), so nothing traverses a reparse point (blocked under
# RedirectionGuard, WinError 448). Falls back to the newest versions/ slot, then
# to a legacy `.venv` reparse target for a host still mid-migration. dotfiles
# #637 (never traverse) + dotfiles #1085 (global binstub marker migration).
$_root = Join-Path $env:USERPROFILE '.agent-worktrees'
$_ver = ''
try { $_ver = ([IO.File]::ReadAllText((Join-Path $_root 'current-version'))).Trim() } catch {}
$_py = if ($_ver) { Join-Path $_root ('versions\' + $_ver + '\Scripts\python.exe') } else { '' }
if (-not ($_py -and (Test-Path -LiteralPath $_py))) { $_py = Get-ChildItem (Join-Path $_root 'versions') -Directory -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object { Join-Path $_.FullName 'Scripts\python.exe' } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -Last 1 }
if (-not ($_py -and (Test-Path -LiteralPath $_py))) {
    $_venv = Join-Path $_root '.venv'
    $_py = Join-Path $_venv 'Scripts\python.exe'
    try { $_t = (Get-Item -LiteralPath $_venv -Force -ErrorAction Stop).Target; if ($_t) { $_py = Join-Path (@($_t)[0]) 'Scripts\python.exe' } } catch {}
}
& $_py -m agent_worktrees @args
exit $LASTEXITCODE
