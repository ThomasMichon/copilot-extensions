$env:PYTHONUTF8 = '1'
# Resolve the venv python WITHOUT traversing the .venv junction: read its reparse
# target and launch the slot python directly. A RedirectionGuard-enforcing process
# is blocked from *traversing* an unprivileged junction but may still *read* its
# target (dotfiles #637). Falls back to .venv\Scripts\python.exe (real dir).
$_venv = Join-Path $env:USERPROFILE '.agent-worktrees\.venv'
$_py = Join-Path $_venv 'Scripts\python.exe'
try { $_t = (Get-Item -LiteralPath $_venv -Force -ErrorAction Stop).Target; if ($_t) { $_py = Join-Path (@($_t)[0]) 'Scripts\python.exe' } } catch {}
& $_py -m agent_worktrees @args
exit $LASTEXITCODE
