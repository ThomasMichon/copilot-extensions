# Canonical agent-worktrees runtime resolver -- dot-sourced by the Windows hooks
# and binstubs. Sets $AwPy to the runtime slot python resolved via the
# junction-free `current-version` marker (the single source of truth; #1106):
#
#   %USERPROFILE%\.agent-worktrees\current-version -> versions\<ver>\Scripts\python.exe
#
# Nothing resolves through the retired `.venv` junction (a reparse point blocked
# by RedirectionGuard, WinError 448/3, and prone to drift). $AwPy is $null when
# no runtime slot is installed (callers degrade gracefully / no-op).
#
# Resolution order (#742): the marker is written atomically (temp + rename), so
# it is never observed half-written or transiently absent during a swap. When it
# IS absent, the fallback prefers the `last-known-good` version (the last version
# the installer activated) over a newest-slot guess, and only guesses the newest
# slot on a true first-run (no marker and no last-known-good). The hot path (a
# present, resolvable marker) is unchanged: last-known-good is read only when the
# marker fails.
#
# Compatible with PowerShell 5.1+ and pwsh 7+.
$AwPy = $null
$_awr = Join-Path $env:USERPROFILE '.agent-worktrees'

# -- helper: return a version's slot python if it exists, else $null --
function _Aw-TrySlot([string]$ver) {
  if (-not $ver) { return $null }
  foreach ($sub in @('Scripts\python.exe', 'bin\python')) {
    $p = Join-Path $_awr ("versions\$ver\$sub")
    if (Test-Path -LiteralPath $p) { return $p }
  }
  return $null
}

# Tier 1: the `current-version` marker (source of truth; atomically written).
$_awv = ''
try { $_awv = ([IO.File]::ReadAllText((Join-Path $_awr 'current-version'))).Trim() } catch {}
if ($_awv) { $AwPy = _Aw-TrySlot $_awv }

# Tier 2: marker absent/stale -> the last version the installer activated
# (`last-known-good`), preferred over a newest-slot guess. Read only here.
if (-not $AwPy) {
  $_awlkg = ''
  try { $_awlkg = ([IO.File]::ReadAllText((Join-Path $_awr 'last-known-good'))).Trim() } catch {}
  if ($_awlkg) { $AwPy = _Aw-TrySlot $_awlkg }
}

# Tier 3: true first-run (no marker, no last-known-good) -> newest installed slot.
if (-not $AwPy) {
  $AwPy = Get-ChildItem (Join-Path $_awr 'versions') -Directory -ErrorAction SilentlyContinue |
    Sort-Object Name |
    ForEach-Object { Join-Path $_.FullName 'Scripts\python.exe' } |
    Where-Object { Test-Path -LiteralPath $_ } |
    Select-Object -Last 1
}
