# Canonical versioned-runtime resolver (PowerShell) -- the single, uniform way a
# binstub, hook, or service launcher resolves a plugin's versioned interpreter.
# Dot-source it after setting the service root; it sets $AgentRtPy:
#
#   $env:AGENT_RT_ROOT = Join-Path $env:USERPROFILE '.agent-<svc>'
#   . <path>\resolve-runtime.ps1
#   if ($AgentRtPy) { & $AgentRtPy -m <module> @args }
#
# Junction-free and identical everywhere: resolves SOLELY the versioned slot
# python via the `current-version` marker, then `last-known-good`, then the
# newest installed slot. It NEVER resolves through a `venv`/`.venv` junction (a
# reparse point RedirectionGuard blocks, WinError 448/3) and NEVER falls back to
# a PATH python -- $AgentRtPy is $null when no runtime is installed, so the
# caller degrades deliberately (self-provision) instead of silently binding the
# system interpreter. Compatible with PowerShell 5.1+ and pwsh 7+.
$AgentRtPy = $null
$_rtRoot = $env:AGENT_RT_ROOT
if ($_rtRoot) {
  $_rtTraceSource = ''
  $_rtTraceVersion = ''

  function _Rt-WriteBootTrace([string]$phase, [string]$extra = '') {
    if (-not $env:COPILOT_EXTENSIONS_BOOT_TRACE) { return }
    $plugin = if ($env:COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN) {
      $env:COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN
    } else {
      (Split-Path -Leaf $_rtRoot).TrimStart('.')
    }
    $line = "::boot-trace:: plugin=$plugin phase=$phase t=$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())"
    if ($extra) { $line += " $extra" }
    [Console]::Error.WriteLine($line)
  }

  function _Rt-MarkerValid([string]$slot, [string]$ver) {
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
  function _Rt-TrySlot([string]$ver) {
    if (-not $ver) { return $null }
    $slot = Join-Path $_rtRoot ("versions\$ver")
    if (-not (_Rt-MarkerValid $slot $ver)) { return $null }
    foreach ($sub in @('Scripts\python.exe', 'bin\python')) {
      $p = Join-Path $slot $sub
      if (Test-Path -LiteralPath $p) { return $p }
    }
    return $null
  }

  function _Rt-VersionKey([string]$ver) {
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
  _Rt-WriteBootTrace 'resolver-marker-start' 'source=current-version'
  $_rtVer = ''
  try { $_rtVer = ([IO.File]::ReadAllText((Join-Path $_rtRoot 'current-version'))).Trim() } catch {}
  if ($_rtVer) { $AgentRtPy = _Rt-TrySlot $_rtVer }
  if ($AgentRtPy) {
    $_rtTraceSource = 'current-version'
    $_rtTraceVersion = $_rtVer
    _Rt-WriteBootTrace 'resolver-marker-result' "source=current-version result=hit version=$_rtVer"
  } else {
    _Rt-WriteBootTrace 'resolver-marker-result' 'source=current-version result=miss'
  }

  # Tier 2: marker absent/stale -> the last version the installer activated.
  if (-not $AgentRtPy) {
    _Rt-WriteBootTrace 'resolver-marker-start' 'source=last-known-good'
    $_rtLkg = ''
    try { $_rtLkg = ([IO.File]::ReadAllText((Join-Path $_rtRoot 'last-known-good'))).Trim() } catch {}
    if ($_rtLkg) { $AgentRtPy = _Rt-TrySlot $_rtLkg }
    if ($AgentRtPy) {
      $_rtTraceSource = 'last-known-good'
      $_rtTraceVersion = $_rtLkg
      _Rt-WriteBootTrace 'resolver-marker-result' "source=last-known-good result=hit version=$_rtLkg"
    } else {
      _Rt-WriteBootTrace 'resolver-marker-result' 'source=last-known-good result=miss'
    }
  }

  # Tier 3: true first-run (no marker, no last-known-good) -> newest complete
  # slot, matching versioned_runtime.resolve_python. Sorted version-aware (each
  # numeric run zero-padded so 0.1.0-dev185 > 0.1.0-dev50, not lexicographic).
  if (-not $AgentRtPy) {
    $_rtSlots = Get-ChildItem (Join-Path $_rtRoot 'versions') -Directory -ErrorAction SilentlyContinue |
      Sort-Object { _Rt-VersionKey $_.Name }
    foreach ($_rtSlot in $_rtSlots) {
      $p = _Rt-TrySlot $_rtSlot.Name
      if ($p) {
        $AgentRtPy = $p
        $_rtTraceSource = 'newest'
        $_rtTraceVersion = $_rtSlot.Name
      }
    }
  }
  if ($AgentRtPy) {
    _Rt-WriteBootTrace 'resolver-slot-result' "source=$_rtTraceSource result=resolved version=$_rtTraceVersion"
  } else {
    _Rt-WriteBootTrace 'resolver-slot-result' 'source=none result=miss'
  }
}
