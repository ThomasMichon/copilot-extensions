# bootstrap-killswitch-guard.ps1 -- canonical, vendored byte-identically into
# every plugin that ships a sessionStart bootstrap-check hook (see
# tools/sync-bootstrap-killswitch.py). PowerShell counterpart of
# bootstrap-killswitch-guard.sh -- see that file's header for the full design
# rationale; this file must stay behaviorally equivalent.
#
# State lives in ONE shared, cross-plugin file:
#   $HOME/.copilot-extensions/bootstrap-killswitch.json
#   {"active": true, "reason": "...", "set_by": "...", "set_at": "..."}
#
# Primary usage -- from the FIRST lines of any bootstrap-check.ps1:
#   $guard = Join-Path $ScriptDir 'bootstrap-killswitch-guard.ps1'
#   if (Test-Path $guard) {
#     & $guard check *>&2
#     if ($LASTEXITCODE -eq 0) { [Console]::Out.Write('{}'); exit 0 }
#   }
# Exit 0 -> killswitch ACTIVE; caller must skip its own reconcile.
# Exit 1 -> killswitch INACTIVE, or state file missing/unreadable/malformed.
#           Fails OPEN to "inactive" deliberately (see .sh header).
#
# Secondary usage -- direct operator/agent control:
#   bootstrap-killswitch-guard.ps1 on "<reason>"
#   bootstrap-killswitch-guard.ps1 off
#   bootstrap-killswitch-guard.ps1 status
param(
  [Parameter(Position = 0)]
  [string]$Command = "check",
  [Parameter(Position = 1)]
  [string]$Reason = ""
)

# Equivalent of bash's `set -e` for `on`/`off`: a failed directory create or
# state write now throws instead of silently continuing to print a success
# message the switch never actually achieved.
$ErrorActionPreference = "Stop"

$StateFile = $env:BOOTSTRAP_KILLSWITCH_STATE_FILE
if (-not $StateFile) {
  $StateFile = Join-Path $HOME ".copilot-extensions\bootstrap-killswitch.json"
}

function Read-KillswitchState {
  if (-not (Test-Path -LiteralPath $StateFile)) { return $null }
  try {
    return (Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json)
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
  $pattern = Join-Path (Join-Path $HOME ".*") "reconcile.lock"
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
    if (Test-Path -LiteralPath $StateFile) {
      Get-Content -LiteralPath $StateFile -Raw
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
    Publish-StateFileAtomically -Path $StateFile -Content ($json + "`n") -Encoding $utf8NoBom
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
    if (Test-Path -LiteralPath $StateFile) { Remove-Item -LiteralPath $StateFile -Force }
    Write-Output "[bootstrap-killswitch] cleared. Plugins resume normal sessionStart reconcile."
  }
  default {
    [Console]::Error.WriteLine("usage: bootstrap-killswitch-guard.ps1 {check|status|on <reason>|off}")
    exit 2
  }
}
