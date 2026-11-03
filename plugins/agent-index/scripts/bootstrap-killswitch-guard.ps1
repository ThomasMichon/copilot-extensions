# bootstrap-killswitch-guard.ps1 -- canonical, vendored byte-identically into
# every plugin that ships a sessionStart bootstrap-check hook (see
# tools/sync-bootstrap-killswitch.py). PowerShell counterpart of
# bootstrap-killswitch-guard.sh -- see that file's header for the full design
# rationale; this file must stay behaviorally equivalent.
#
# State lives in ONE shared, cross-plugin file:
#   $HOME/.copilot-extensions/killSwitches/bootstrap-killswitch.json
#   {"active": true, "reason": "...", "set_by": "...", "set_at": "..."}
#
# Primary usage -- from the FIRST lines of any bootstrap-check.ps1:
#   $guard = Join-Path $ScriptDir 'bootstrap-killswitch-guard.ps1'
#   if (Test-Path $guard) {
#     & $guard check
#     if ($LASTEXITCODE -eq 0) { [Console]::Out.Write('{}'); exit 0 }
#   }
# (PowerShell has no stream-merge-into-stderr operator analogous to bash's
# `>&2` -- only `N>&1` merging into the success stream is supported, so
# there's nothing valid to redirect here. Not a problem in practice: `check`
# never writes to stdout -- its only output is via [Console]::Error.WriteLine,
# which writes straight to the real stderr handle regardless of any
# PowerShell-level redirection.)
# Exit 0 -> killswitch ACTIVE; caller must skip its own reconcile.
# Exit 1 -> killswitch INACTIVE, or state file missing/unreadable/malformed.
#           Fails OPEN to "inactive" deliberately (see .sh header).
#
# Secondary usage -- direct operator/agent control:
#   bootstrap-killswitch-guard.ps1 on "<reason>"
#   bootstrap-killswitch-guard.ps1 off
#   bootstrap-killswitch-guard.ps1 status
[CmdletBinding(PositionalBinding = $false)]
param(
  [Parameter(Position = 0)]
  [string]$Command = "check",
  [Parameter(Position = 1)]
  [string]$Reason = "",
  [ValidatePattern('^$|^[a-f0-9]{64}$')]
  [string]$ExpectedState = ""
)

# Equivalent of bash's `set -e` for `on`/`off`: a failed directory create or
# state write now throws instead of silently continuing to print a success
# message the switch never actually achieved.
$ErrorActionPreference = "Stop"

if ($Command -ceq "capabilities") {
  if ($Reason -or $ExpectedState) { throw "Capabilities does not accept mutation arguments" }
  Write-Output '{"schema":"copilot-extensions.bootstrap-killswitch-controller","version":1,"capabilities":["conditional-mutation-v1"]}'
  exit 0
}

function Resolve-StateFilePath {
  param([string]$Path)
  if ($Path -match '^~([\\/]|$)') {
    $userHome = $HOME
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
      if ($env:USERPROFILE) { $userHome = $env:USERPROFILE }
      elseif ($env:HOMEPATH) { $userHome = "$env:HOMEDRIVE$env:HOMEPATH" }
    } elseif ($env:HOME) { $userHome = $env:HOME }
    $Path = if ($Path.Length -eq 1) { $userHome } else { Join-Path $userHome $Path.Substring(2) }
  } elseif ($Path.StartsWith("~")) {
    throw "Named-user state-file overrides require an explicitly expanded path"
  }
  if (-not [IO.Path]::IsPathRooted($Path)) {
    $location = Get-Location
    if ($location.Provider.Name -ne "FileSystem") {
      throw "Relative state-file overrides require a filesystem location"
    }
    $Path = Join-Path $location.ProviderPath $Path
  }
  return [IO.Path]::GetFullPath($Path)
}

$StateFile = $env:BOOTSTRAP_KILLSWITCH_STATE_FILE
$LegacyStateFile = $null
if (-not $StateFile) {
  $StateFile = Join-Path $HOME ".copilot-extensions\killSwitches\bootstrap-killswitch.json"
  $LegacyStateFile = Join-Path $HOME ".copilot-extensions\bootstrap-killswitch.json"
}
$StateFile = Resolve-StateFilePath $StateFile
if ($LegacyStateFile) { $LegacyStateFile = Resolve-StateFilePath $LegacyStateFile }
$ReadStateFile = $StateFile
if ($LegacyStateFile -and -not (Test-Path -LiteralPath $StateFile) -and
    (Test-Path -LiteralPath $LegacyStateFile)) {
  $ReadStateFile = $LegacyStateFile
}

function Read-KillswitchState {
  if (-not (Test-Path -LiteralPath $ReadStateFile)) { return $null }
  try {
    return (Get-Content -LiteralPath $ReadStateFile -Raw | ConvertFrom-Json)
  } catch {
    return $null
  }
}

function Get-LiveReconcilingPlugins {
  # Best-effort: the switch only prevents a NEW reconcile from starting; it
  # cannot stop one already running (a detached background process that
  # outlives the session-start hook that spawned it). Most adopters guard
  # their own background reconcile with a `~/.<plugin>/reconcile.lock`
  # single-flight file naming the live PID -- check that convention across
  # every such lock this host knows about. Not exhaustive: a handful of
  # adopters (agent-bridge, agent-machines, agent-worktrees) don't use this
  # exact lock convention and aren't detected here -- see README.md's Known
  # limitations.
  $found = @()
  $scanRoot = $env:BOOTSTRAP_KILLSWITCH_RECONCILE_SCAN_ROOT
  if (-not $scanRoot) { $scanRoot = $HOME }
  $pattern = Join-Path (Join-Path $scanRoot ".*") "reconcile.lock"
  foreach ($lock in (Get-ChildItem -Path $pattern -Force -ErrorAction SilentlyContinue)) {
    try {
      $pidText = (Get-Content -LiteralPath $lock.FullName -Raw).Trim()
      $procId = [int]$pidText
    } catch {
      continue
    }
    if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
      $found += $lock.Directory.Name.TrimStart('.')
    }
  }
  return $found
}

function Publish-StateFileAtomically {
    # Write a file's content atomically: write to a same-directory temp
    # file, then replace/rename into place. `Move-Item -Force` does NOT
    # guarantee an atomic replace on every supported PowerShell runtime --
    # notably Windows PowerShell 5.1, whose -Force implementation can
    # delete the existing destination before moving the new one in,
    # leaving a brief window with NO destination file at all (worse than a
    # torn write for a concurrent `check`: it would see "missing", not
    # "malformed", but both fail open the same way -- the fix is the same
    # either way). [System.IO.File]::Replace() IS an atomic NTFS replace;
    # use it whenever a destination already exists, and fall back to a
    # plain (non-forcing) Move-Item -- itself an atomic rename -- for a
    # first-ever publish. Mirrors the exact, already-reviewed pattern in
    # plugins/agent-machines/scripts/init.ps1's own Publish-FileAtomically
    # (this guard cannot import that plugin's script -- every adopter
    # vendors its own independent copy -- so the proven pattern is ported
    # here directly rather than re-derived).
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Content,
        [Parameter(Mandatory)]$Encoding
    )
    $fullPath = [IO.Path]::GetFullPath($Path)
    $tmp = "$fullPath.tmp-$PID"
    [System.IO.File]::WriteAllText($tmp, $Content, $Encoding)
    for ($attempt = 1; $true; $attempt++) {
        try {
            if (Test-Path -LiteralPath $fullPath) {
                $backup = "$fullPath.bak-$PID"
                [System.IO.File]::Replace($tmp, $fullPath, $backup)
                Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
            } else {
                Move-Item -LiteralPath $tmp -Destination $fullPath
            }
            return
        } catch {
            if ($attempt -ge 20) { throw }
            Start-Sleep -Milliseconds 15
        }
    }
}

function Get-StateRevision {
  param([string]$Path)
  $fullPath = [IO.Path]::GetFullPath($Path)
  $prefix = [Text.Encoding]::UTF8.GetBytes($fullPath + [char]0)
  if ([IO.File]::Exists($fullPath)) {
    $marker = [Text.Encoding]::UTF8.GetBytes("present" + [char]0)
    [byte[]]$bytes = $prefix + $marker + [IO.File]::ReadAllBytes($fullPath)
  } else {
    [byte[]]$bytes = $prefix + [Text.Encoding]::UTF8.GetBytes("absent")
  }
  $sha = [Security.Cryptography.SHA256]::Create()
  try {
    return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
  } finally {
    $sha.Dispose()
  }
}

function Invoke-StateMutation {
  param([string]$Path, [string]$Expected, [scriptblock]$Action)
  $fullPath = [IO.Path]::GetFullPath($Path)
  [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($fullPath)) | Out-Null
  $lockPath = "$fullPath.lock"
  foreach ($candidate in @($fullPath, $lockPath)) {
    if ((Test-Path -LiteralPath $candidate) -and
        (([IO.File]::GetAttributes($candidate) -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
      throw "Automatic-update switch or writer lock is linked"
    }
  }
  $stream = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate,
    [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite)
  $locked = $false
  try {
    if ($stream.Length -eq 0) { $stream.WriteByte(0); $stream.Flush() }
    $deadline = [Diagnostics.Stopwatch]::StartNew()
    while (-not $locked) {
      try {
        $stream.Lock(0, 1)
        $locked = $true
      } catch [IO.IOException] {
        if ($deadline.Elapsed.TotalSeconds -ge 5) {
          throw "Automatic-update switch writer lock unavailable: $($_.Exception.Message)"
        }
        Start-Sleep -Milliseconds 25
      }
    }
    if ($Expected -and (Get-StateRevision $fullPath) -cne $Expected) {
      throw "Automatic-update switch changed; refresh before applying"
    }
    & $Action
  } finally {
    if ($locked) { $stream.Unlock(0, 1) }
    $stream.Dispose()
  }
}

if ($ExpectedState -and $Command -cnotin @("on", "off")) {
  throw "ExpectedState applies only to switch mutations"
}
if ($ExpectedState -and $Command -ceq "off" -and $LegacyStateFile) {
  throw "Conditional mutation requires an explicit state-file override"
}

switch ($Command) {
  "check" {
    $state = Read-KillswitchState
    if ($null -ne $state -and $state.active -eq $true) {
      $r = $state.reason
      if ($r) {
        [Console]::Error.WriteLine("[bootstrap-killswitch] ACTIVE -- $r; skipping automatic reconcile.")
      } else {
        [Console]::Error.WriteLine("[bootstrap-killswitch] ACTIVE; skipping automatic reconcile.")
      }
      exit 0
    }
    exit 1
  }
  "status" {
    if (Test-Path -LiteralPath $ReadStateFile) {
      Get-Content -LiteralPath $ReadStateFile -Raw
    } else {
      Write-Output '{"active": false}'
    }
  }
  "on" {
    $dir = Split-Path -Parent $StateFile
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $who = "$env:USERNAME@$env:COMPUTERNAME"
    $data = [ordered]@{
      active = $true
      reason = $Reason
      set_by = $who
      set_at = (Get-Date).ToUniversalTime().ToString("o")
    }
    # Write BOM-less UTF-8 (Set-Content -Encoding utf8 writes a BOM on
    # Windows PowerShell 5.1, which Python's json.load -- used by both the
    # Bash guard and the agent-machines CLI -- rejects, so an activation
    # through this surface could be reported as inactive by the other two),
    # via the same atomic replace-or-move helper used for every mutation.
    $json = $data | ConvertTo-Json
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    Invoke-StateMutation -Path $StateFile -Expected $ExpectedState -Action {
      Publish-StateFileAtomically -Path $StateFile -Content ($json + "`n") -Encoding $utf8NoBom
    }
    Write-Output "[bootstrap-killswitch] ACTIVE$(if ($Reason) { " -- $Reason" })."
    Write-Output "Every plugin's sessionStart reconcile is now paused on this machine."
    $live = Get-LiveReconcilingPlugins
    if ($live.Count -gt 0) {
      Write-Output "WARNING: the switch only prevents a NEW reconcile from starting -- it"
      Write-Output "cannot stop one already running. These plugin(s) have a reconcile in"
      Write-Output "flight right now (detached; outlives this command):"
      foreach ($name in $live) { Write-Output "  - $name" }
      Write-Output "Wait for it/them to finish before treating their venv as settled."
    }
    Write-Output "Reset with: agent-machines bootstrap-killswitch off"
  }
  "off" {
    foreach ($path in @($StateFile, $LegacyStateFile)) {
      if ($path) {
        Invoke-StateMutation -Path $path -Expected $ExpectedState -Action {
          if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
        }
      }
    }
    Write-Output "[bootstrap-killswitch] cleared. Plugins resume normal sessionStart reconcile."
  }
  default {
    [Console]::Error.WriteLine("usage: bootstrap-killswitch-guard.ps1 {check|status|capabilities|on <reason>|off} [-ExpectedState REVISION]")
    exit 2
  }
}
