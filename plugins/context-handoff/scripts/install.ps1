#Requires -Version 7.0
<#
.SYNOPSIS
    context-handoff extension installer -- payload runtime (non-Python).

.DESCRIPTION
    Deploys the context-handoff Copilot CLI session extension. Unlike the
    Python-runtime plugins in this repo, the runtime here is a single
    JavaScript extension file plus a Copilot CLI settings flag -- there is no
    venv, binstub, or service. The install contract's payload-runtime variant
    applies (see docs/install-contract.md).

    Actions:
      install / update  Copy extension.mjs to ~/.copilot/extensions/, ensure
                        experimental:true in settings.json, write the manifest.
      uninstall         Remove the deployed extension + manifest (the
                        experimental flag is left intact).
      status            Report deployment state.

    Run from the plugin source dir (marketplace vendor copy or local checkout):
      pwsh -File scripts/install.ps1 update
      pwsh -File scripts/install.ps1 status
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'uninstall', 'status', 'update')]
    [string]$Action = 'status'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# -- Metadata / paths -----------------------------------------------------

$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginDir    = (Resolve-Path (Join-Path $ScriptDir '..')).Path
$PayloadSrc   = Join-Path $PluginDir 'extension\context-handoff\extension.mjs'
$ExtDir       = Join-Path $env:USERPROFILE '.copilot\extensions\context-handoff'
$ExtTarget    = Join-Path $ExtDir 'extension.mjs'
$RuntimeDir   = Join-Path $env:USERPROFILE '.context-handoff'
$SettingsPath = Join-Path $env:USERPROFILE '.copilot\settings.json'

# -- Output helpers (ASCII-safe; no UTF-8 context assumed) ----------------

function Write-Ok   { param([string]$m) Write-Host "[OK]   $m" }
function Write-Info { param([string]$m) Write-Host "[..]   $m" }
function Write-Warn { param([string]$m) Write-Host "[WARN] $m" }
function Write-Err  { param([string]$m) Write-Host "[FAIL] $m" }

function Get-PluginVersion {
    $pj = Join-Path $PluginDir 'plugin.json'
    if (Test-Path $pj) {
        try { return (Get-Content $pj -Raw | ConvertFrom-Json).version } catch { return '0.0.0' }
    }
    return '0.0.0'
}

function Get-GitInfo {
    param([string]$Path)
    try {
        $commit = git -C $Path rev-parse --short HEAD 2>$null
        $branch = git -C $Path rev-parse --abbrev-ref HEAD 2>$null
        $dirty = $false
        $dirtyOut = git -C $Path status --porcelain 2>$null
        if ($dirtyOut) { $dirty = $true }
        return @{
            commit = $(if ($commit) { $commit } else { 'unknown' })
            branch = $(if ($branch) { $branch } else { 'unknown' })
            dirty  = $dirty
        }
    } catch {
        return @{ commit = 'unknown'; branch = 'unknown'; dirty = $false }
    }
}

# === install-contract:v3 source-kind -- keep byte-identical across plugins ===
# A runtime footprint's source is inferred from where the installer runs.
# Vendored under the Copilot CLI installed-plugins dir => marketplace;
# anything else (a git checkout) => local. `update` re-installs from whatever
# the recorded footprint is, because the same installer is invoked from the
# same place.
function Get-SourceKind {
    param([string]$PluginPath)
    if (($PluginPath -replace '\\', '/') -match '/\.copilot/installed-plugins/') {
        return 'marketplace'
    }
    return 'local'
}
# === end install-contract:v3 source-kind ===

# Unified schema_version 3 manifest writer. Self-contained per plugin (no shared
# module -- plugins are pulled independently from the marketplace). Records the
# source footprint (local vs marketplace) and is written atomically (temp+move).
# Payload-runtime variant: no venv; `runtime` is the extension load path.
function Write-DeployManifest {
    $manifestPath = Join-Path $RuntimeDir 'deploy-manifest.json'
    $kind = Get-SourceKind -PluginPath $PluginDir
    $ver = Get-PluginVersion

    # Git provenance only applies to a local checkout -- the marketplace vendor
    # copy is not a git repo.
    $commit = $null; $branch = $null; $dirty = $false
    if ($kind -eq 'local') {
        $gitInfo = Get-GitInfo -Path (Split-Path $PluginDir)
        $commit = $gitInfo.commit; $branch = $gitInfo.branch; $dirty = $gitInfo.dirty
    }

    $manifest = [ordered]@{
        schema_version = 3
        service        = 'context-handoff'
        deployed_at    = (Get-Date -Format 'o')
        deployed_by    = "$($env:COMPUTERNAME.ToLower())-windows"
        source         = [ordered]@{
            kind    = $kind
            path    = ($PluginDir -replace '\\', '/')
            repo    = 'copilot-extensions'
            plugin  = 'context-handoff'
            version = $ver
            commit  = $commit
            branch  = $branch
            dirty   = $dirty
        }
        venv           = $null
        runtime        = 'extension'
        extension_path = ($ExtTarget -replace '\\', '/')
    }

    if (-not (Test-Path $RuntimeDir)) { New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null }
    $tmp = "$manifestPath.tmp"
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $tmp -Encoding UTF8
    Move-Item -Force -Path $tmp -Destination $manifestPath
    Write-Ok "Deploy manifest written (source: $kind, version: $ver)"
}

# Ensure experimental:true in settings.json. Extensions are gated behind it --
# the COPILOT_FEATURE_FLAGS env var alone is insufficient. Idempotent; preserves
# all other settings; atomic temp+move write.
function Set-ExperimentalFlag {
    if (Test-Path $SettingsPath) {
        try { $s = Get-Content $SettingsPath -Raw | ConvertFrom-Json } catch {
            Write-Warn "settings.json is not valid JSON -- not modifying it; set experimental:true manually"
            return
        }
        $has = ($s.PSObject.Properties.Name -contains 'experimental')
        if ($has -and $s.experimental -eq $true) {
            Write-Info "experimental:true already set in settings.json"
            return
        }
        if ($has) { $s.experimental = $true }
        else { $s | Add-Member -NotePropertyName experimental -NotePropertyValue $true }
        $tmp = "$SettingsPath.tmp"
        $s | ConvertTo-Json -Depth 100 | Set-Content -Path $tmp -Encoding UTF8
        Move-Item -Force -Path $tmp -Destination $SettingsPath
        Write-Ok "Set experimental:true in settings.json"
    } else {
        $dir = Split-Path -Parent $SettingsPath
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
        '{
  "experimental": true
}' | Set-Content -Path $SettingsPath -Encoding UTF8
        Write-Ok "Created settings.json with experimental:true"
    }
}

function Invoke-Install {
    if (-not (Test-Path $PayloadSrc)) {
        Write-Err "Extension payload not found at $PayloadSrc"
        exit 1
    }
    if (-not (Test-Path $ExtDir)) { New-Item -ItemType Directory -Force -Path $ExtDir | Out-Null }
    Copy-Item -Force $PayloadSrc $ExtTarget
    Write-Ok "Deployed extension.mjs -> $ExtTarget"
    Set-ExperimentalFlag
    Write-DeployManifest
    Write-Info "Activates on the NEXT Copilot CLI session (extensions are scanned at startup)."
}

function Invoke-Uninstall {
    if (Test-Path $ExtTarget) { Remove-Item -Force $ExtTarget; Write-Ok "Removed $ExtTarget" }
    if ((Test-Path $ExtDir) -and -not (Get-ChildItem $ExtDir -Force)) { Remove-Item -Force $ExtDir }
    $manifestPath = Join-Path $RuntimeDir 'deploy-manifest.json'
    if (Test-Path $manifestPath) { Remove-Item -Force $manifestPath; Write-Ok "Removed deploy manifest" }
    if ((Test-Path $RuntimeDir) -and -not (Get-ChildItem $RuntimeDir -Force)) { Remove-Item -Force $RuntimeDir }
    Write-Info "Left experimental:true in settings.json untouched (other extensions may need it)."
}

function Invoke-Status {
    Write-Host "context-handoff extension status"
    Write-Host "  source dir   : $PluginDir"
    Write-Host "  source kind  : $(Get-SourceKind -PluginPath $PluginDir)"
    Write-Host "  plugin ver   : $(Get-PluginVersion)"
    if (Test-Path $ExtTarget) { Write-Ok "deployed     : $ExtTarget" }
    else { Write-Warn "deployed     : NOT deployed ($ExtTarget missing)" }
    $expSet = $false
    if (Test-Path $SettingsPath) {
        try { $expSet = ((Get-Content $SettingsPath -Raw | ConvertFrom-Json).experimental -eq $true) } catch { $expSet = $false }
    }
    if ($expSet) { Write-Ok "experimental : true" } else { Write-Warn "experimental : NOT set -- extension will not load" }
    $manifestPath = Join-Path $RuntimeDir 'deploy-manifest.json'
    if (Test-Path $manifestPath) { Write-Ok "manifest     : $manifestPath" }
    else { Write-Warn "manifest     : none" }
}

switch ($Action) {
    'install'   { Invoke-Install }
    'update'    { Invoke-Install }
    'uninstall' { Invoke-Uninstall }
    'status'    { Invoke-Status }
}
