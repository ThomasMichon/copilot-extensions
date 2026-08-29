<#
readiness-context -- agent-* runtime sessionStart hook (PowerShell).

Emits an AFFIRMATIVE readiness confirmation as {"additionalContext": "..."} so a
session (especially one where ONLY this plugin was installed) knows whether the
plugin's CLI is actually usable, or what to do next.

READY means either the payload-local self-provisioning command is usable or a
legacy management binstub has a complete runtime. Anything else is reported
NOT READY with the next step. Absence of an affirmative "ready" is treated as
"not set up"; never infer ready from the absence of error.

MUST run even when the plugin's OWN runtime is not provisioned (the case it
reports), so it is pure PowerShell + stdlib python (only to read plugin.json's
name), never the plugin's venv. Generic + self-locating; parity with the .sh.
#>
$ErrorActionPreference = 'SilentlyContinue'
$Aggregate = $args -contains '--aggregate'

function Emit([string]$msg) {
  if ($Aggregate) {
    if ($msg.Contains('NOT READY')) {
      $msg = "[owner: $name@$version]`n$name is NOT READY; restart to provision it and do not use it until it reports READY. Use the ``agent-codespaces`` skill for setup details."
    } else {
      $msg = "[owner: $name@$version]`n$name is READY; invoke its exact session-catalog command. Use the ``agent-codespaces`` skill for lifecycle details."
    }
  }
  $obj = @{ additionalContext = $msg } | ConvertTo-Json -Compress
  [Console]::Out.Write($obj); exit 0
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginDir = Split-Path -Parent $ScriptDir
$name = ''
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
  $py = Get-Command python3 -ErrorAction SilentlyContinue
}
if ($py) { $name = (& $py.Source -c 'import json,sys;print(json.load(open(sys.argv[1])).get("name",""))' "$PluginDir/plugin.json" 2>$null) }
if (-not $name) { $name = Split-Path -Leaf $PluginDir }
if (-not $name) { exit 0 }
$name = $name.Trim()
$version = 'unknown'
try {
  $version = [string]((Get-Content -Raw -LiteralPath "$PluginDir/plugin.json" | ConvertFrom-Json).version)
} catch {}
if (-not $version) { $version = 'unknown' }

$InstallDir = Join-Path $HOME ".$name"
$Binstub    = Join-Path $HOME ".local/bin/$name"
$BinstubWin = Join-Path $HOME ".local/bin/$name.cmd"
$PayloadCommand = Join-Path $PluginDir "bin/$name.ps1"
$PayloadInstaller = Join-Path $PluginDir 'scripts/install.ps1'
$ver = ''
$verFile = Join-Path $InstallDir 'current-version'
if (Test-Path $verFile) { $ver = (Get-Content $verFile -Raw).Trim() }

$venvOk = $false
if ($ver) {
  # READY iff the current-version marker's slot interpreter exists (marker-only;
  # the retired `.venv` link is no longer probed -- uniform-runtime-resolution #765).
  foreach ($sub in @("versions/$ver/Scripts/python.exe","versions/$ver/bin/python")) {
    if (Test-Path (Join-Path $InstallDir $sub)) { $venvOk = $true; break }
  }
}
$binOk = (Test-Path $Binstub) -or (Test-Path $BinstubWin)
if ((Test-Path $PayloadCommand) -and (Test-Path $PayloadInstaller)) {
  if ($venvOk) {
    Emit "$name`: READY -- payload-local command available; runtime $ver provisioned."
  } else {
    Emit "$name`: READY -- payload-local command available; runtime provisions on first use."
  }
}
if ($binOk -and $venvOk) {
  Emit "$name`: READY -- legacy management command available; runtime $ver provisioned."
}

$setup = "bash `"$PluginDir/scripts/install.sh`" install (or scripts/install.ps1 install on Windows)"
if ((Test-Path (Join-Path $InstallDir 'deploy-manifest.json')) -or $ver) {
  Emit "$name`: NOT READY -- runtime is provisioning or incomplete (its CLI is not yet on PATH). RESTART this session to pick it up; if it persists after a restart, run: $setup . Do NOT attempt $name operations until it reports READY."
}
Emit "$name`: NOT READY -- runtime is not provisioned (fresh install; no CLI on PATH). Provision it by RESTARTING this session (first-session provisioning), or run: $setup . Do NOT attempt $name operations until it reports READY."
