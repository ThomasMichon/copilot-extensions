# Canonical agent-worktrees runtime resolver -- dot-sourced by the Windows hooks
# and binstubs. Sets $AwPy and the payload-invocation contract's $AgentRtPy to
# the runtime slot python resolved via the
# junction-free `current-version` marker (the single source of truth; #1106):
#
#   %USERPROFILE%\.agent-worktrees\current-version -> versions\<ver>\Scripts\python.exe
#
# Nothing resolves through the retired `.venv` junction (a reparse point blocked
# by RedirectionGuard, WinError 448/3, and prone to drift). Both variables are
# $null when no runtime slot is installed (callers degrade gracefully / no-op).
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

function _Aw-MarkerValid([string]$slot, [string]$ver) {
  if (-not $ver) { return $false }
  try {
    $raw = [IO.File]::ReadAllText((Join-Path $slot '.install-complete.json'))
    if ([regex]::Matches($raw, '"version"\s*:').Count -ne 1) { return $false }
    $marker = $raw | ConvertFrom-Json -ErrorAction Stop
    return ($marker -is [pscustomobject]) -and ([string]$marker.version -ceq $ver)
  } catch {
    return $false
  }
}

# -- helper: return a complete version's slot python, else $null --
function _Aw-TrySlot([string]$ver) {
  if (-not $ver) { return $null }
  $slot = Join-Path $_awr ("versions\$ver")
  if (-not (_Aw-MarkerValid $slot $ver)) { return $null }
  foreach ($sub in @('Scripts\python.exe', 'bin\python')) {
    $p = Join-Path $slot $sub
    if (Test-Path -LiteralPath $p) { return $p }
  }
  return $null
}

function _Aw-VersionKey([string]$ver) {
  if ($ver -match '^(\d+)\.(\d+)\.(\d+)(?:-dev(\d+))?$') {
    $phase = if ($Matches[4]) { '0' } else { '1' }
    $dev = if ($Matches[4]) { $Matches[4] } else { '0' }
    return '0:{0}.{1}.{2}.{3}.{4}' -f
      $Matches[1].PadLeft(20, '0'),
      $Matches[2].PadLeft(20, '0'),
      $Matches[3].PadLeft(20, '0'),
      $phase,
      $dev.PadLeft(20, '0')
  }
  return '1:' + [regex]::Replace(
    $ver.ToLowerInvariant(), '\d+',
    { param($m) $m.Value.PadLeft(20, '0') }
  )
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

# Tier 3: true first-run -> newest complete installed slot.
if (-not $AwPy) {
  $AwPy = Get-ChildItem (Join-Path $_awr 'versions') -Directory -ErrorAction SilentlyContinue |
    Sort-Object { _Aw-VersionKey $_.Name } |
    ForEach-Object { _Aw-TrySlot $_.Name } |
    Where-Object { $_ } | Select-Object -Last 1
}

$AgentRtPy = $AwPy
