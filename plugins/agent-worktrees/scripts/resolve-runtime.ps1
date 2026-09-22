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
$_awr = if ($env:AGENT_RT_ROOT) {
  $env:AGENT_RT_ROOT
} else {
  Join-Path $env:USERPROFILE '.agent-worktrees'
}
$_awTraceSource = ''
$_awTraceVersion = ''

function _Aw-WriteBootTrace([string]$phase, [string]$extra = '') {
  if (-not $env:COPILOT_EXTENSIONS_BOOT_TRACE) { return }
  $plugin = if ($env:COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN) {
    $env:COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN
  } else {
    (Split-Path -Leaf $_awr).TrimStart('.')
  }
  $line = "::boot-trace:: plugin=$plugin phase=$phase t=$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())"
  if ($extra) { $line += " $extra" }
  [Console]::Error.WriteLine($line)
}

function _Aw-MarkerValid([string]$slot, [string]$ver) {
  if (-not $ver) { return $false }
  try {
    $raw = [IO.File]::ReadAllText((Join-Path $slot '.install-complete.json'))
    if ($raw -cnotmatch '^\{"version": "[^"\\]+", "completed_at": "[^"\\]+", "pid": (0|[1-9][0-9]*)(, "payload_hash": "[^"\\]+")?\}$') {
      return $false
    }
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
_Aw-WriteBootTrace 'resolver-marker-start' 'source=current-version'
$_awv = ''
try { $_awv = ([IO.File]::ReadAllText((Join-Path $_awr 'current-version'))).Trim() } catch {}
if ($_awv) { $AwPy = _Aw-TrySlot $_awv }
if ($AwPy) {
  $_awTraceSource = 'current-version'
  $_awTraceVersion = $_awv
  _Aw-WriteBootTrace 'resolver-marker-result' "source=current-version result=hit version=$_awv"
} else {
  _Aw-WriteBootTrace 'resolver-marker-result' 'source=current-version result=miss'
}

# Tier 2: marker absent/stale -> the last version the installer activated
# (`last-known-good`), preferred over a newest-slot guess. Read only here.
if (-not $AwPy) {
  _Aw-WriteBootTrace 'resolver-marker-start' 'source=last-known-good'
  $_awlkg = ''
  try { $_awlkg = ([IO.File]::ReadAllText((Join-Path $_awr 'last-known-good'))).Trim() } catch {}
  if ($_awlkg) { $AwPy = _Aw-TrySlot $_awlkg }
  if ($AwPy) {
    $_awTraceSource = 'last-known-good'
    $_awTraceVersion = $_awlkg
    _Aw-WriteBootTrace 'resolver-marker-result' "source=last-known-good result=hit version=$_awlkg"
  } else {
    _Aw-WriteBootTrace 'resolver-marker-result' 'source=last-known-good result=miss'
  }
}

# Tier 3: true first-run -> newest complete installed slot.
if (-not $AwPy) {
  $AwPy = Get-ChildItem (Join-Path $_awr 'versions') -Directory -ErrorAction SilentlyContinue |
    Sort-Object { _Aw-VersionKey $_.Name } |
    ForEach-Object {
      $resolved = _Aw-TrySlot $_.Name
      if ($resolved) {
        $_awTraceSource = 'newest'
        $_awTraceVersion = $_.Name
      }
      $resolved
    } |
    Where-Object { $_ } | Select-Object -Last 1
}
if ($AwPy) {
  _Aw-WriteBootTrace 'resolver-slot-result' "source=$_awTraceSource result=resolved version=$_awTraceVersion"
} else {
  _Aw-WriteBootTrace 'resolver-slot-result' 'source=none result=miss'
}

$AgentRtPy = $AwPy
