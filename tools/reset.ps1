<#
.SYNOPSIS
    One-shot teardown / baseline reset for the copilot-extensions suite.

.DESCRIPTION
    Stops the agent-bridge daemon + credential relay, removes all three plugin
    runtimes (~/.agent-worktrees, ~/.agent-bridge, ~/.agent-codespaces), their
    binstubs, project binstubs, the Windows Terminal fragment, psmux config,
    and the scheduled task -- so a machine returns to a clean baseline without
    manual process-killing or filesystem sweeps.

    Idempotent and dependency-free: works even when the binstubs/CLIs are
    broken. Does NOT touch your source repos or their .worktrees content.

.PARAMETER RemovePlugins
    Also uninstall the marketplace plugins (copilot plugin uninstall) and
    remove the marketplace registration.

.PARAMETER RemoveProjectConfigs
    Also remove per-project config dirs (~/.<project>) created by
    `agent-worktrees register`. These hold per-repo worktree config, not the
    repos themselves.

.PARAMETER Yes
    Do not prompt for confirmation.
#>
[CmdletBinding()]
param(
    [switch]$RemovePlugins,
    [switch]$RemoveProjectConfigs,
    [switch]$Yes
)

$ErrorActionPreference = 'Continue'

function Step($m) { Write-Host "  ...    $m" -ForegroundColor DarkGray }
function Ok($m)   { Write-Host "  [OK]   $m" -ForegroundColor Green }
function Skip($m) { Write-Host "  [SKIP] $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  [WARN] $m" -ForegroundColor Yellow }

$Home_       = $env:USERPROFILE
$LocalBin    = Join-Path $Home_ '.local\bin'
$Runtimes    = @(
    (Join-Path $Home_ '.agent-worktrees'),
    (Join-Path $Home_ '.agent-bridge'),
    (Join-Path $Home_ '.agent-codespaces')
)
$Binstubs    = @('agent-worktrees', 'agent-bridge', 'agent-codespaces')
$Ports       = @(9280, 9281, 9857)   # bridge (win/wsl) + credential relay
$TaskName    = 'Agent Bridge'
$WtFragment  = Join-Path $env:LOCALAPPDATA 'Microsoft\Windows Terminal\Fragments\AgentWorktrees'
$PsmuxConf   = Join-Path $Home_ '.psmux.conf'

Write-Host ''
Write-Host '=== copilot-extensions reset ===' -ForegroundColor Cyan
Write-Host ''

if (-not $Yes) {
    Write-Host 'This removes the agent-worktrees / agent-bridge / agent-codespaces'
    Write-Host 'runtimes, binstubs, service, and config. Source repos are untouched.'
    if ($RemovePlugins)        { Write-Host '  + marketplace plugins will be uninstalled' }
    if ($RemoveProjectConfigs) { Write-Host '  + per-project ~/.<project> config dirs will be removed' }
    $ans = Read-Host 'Proceed? (y/N)'
    if ($ans -notmatch '^(y|yes)$') { Write-Host 'Aborted.'; exit 0 }
    Write-Host ''
}

# -- 1. Stop the scheduled task ------------------------------------------------
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Ok "Removed scheduled task '$TaskName'"
} else {
    Skip "Scheduled task '$TaskName' not present"
}

# -- 2. Kill anything still bound to the bridge / relay ports ------------------
foreach ($port in $Ports) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $procId = $c.OwningProcess
        if ($procId -and $procId -ne 0) {
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            Ok "Killed process on port $port (pid=$procId)"
        }
    }
}

# -- 3. Best-effort: run each plugin's own uninstall (tested cleanup) ----------
$pluginRoot = Join-Path $Home_ '.copilot\installed-plugins'
function Find-PluginScript($pluginName, $script) {
    if (-not (Test-Path $pluginRoot)) { return $null }
    $hit = Get-ChildItem -Recurse -Path $pluginRoot -Filter plugin.json -ErrorAction SilentlyContinue |
        Where-Object { (Get-Content $_.FullName -Raw) -match "`"$pluginName`"" } |
        Select-Object -First 1
    if ($hit) {
        $p = Join-Path $hit.DirectoryName "scripts\$script"
        if (Test-Path $p) { return $p }
    }
    return $null
}

$awUninstall = Find-PluginScript 'agent-worktrees' 'install.ps1'
if ($awUninstall) {
    Step 'Running agent-worktrees uninstall...'
    & powershell -NoProfile -ExecutionPolicy Bypass -File $awUninstall uninstall -RemoveConfig -Force *> $null
}
$abUninstall = Find-PluginScript 'agent-bridge' 'install.ps1'
if ($abUninstall) {
    Step 'Running agent-bridge uninstall...'
    & powershell -NoProfile -ExecutionPolicy Bypass -File $abUninstall uninstall -Purge *> $null
}
$acUninstall = Find-PluginScript 'agent-codespaces' 'install.ps1'
if ($acUninstall) {
    Step 'Running agent-codespaces uninstall...'
    & powershell -NoProfile -ExecutionPolicy Bypass -File $acUninstall uninstall *> $null
}

# -- 4. Hard sweep (idempotent -- catches partial / init-only installs) --------
foreach ($rt in $Runtimes) {
    if (Test-Path $rt) {
        Remove-Item $rt -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path $rt) { Warn "Could not fully remove $rt (in use?)" } else { Ok "Removed $rt" }
    }
}

# Core binstubs
foreach ($name in $Binstubs) {
    foreach ($ext in @('.cmd', '')) {
        $stub = Join-Path $LocalBin "$name$ext"
        if (Test-Path $stub) { Remove-Item $stub -Force -ErrorAction SilentlyContinue; Ok "Removed binstub $name$ext" }
    }
}

# Project binstubs: any ~/.local/bin/*.cmd that launches the worktree runtime
if (Test-Path $LocalBin) {
    Get-ChildItem -Path $LocalBin -Filter '*.cmd' -ErrorAction SilentlyContinue | ForEach-Object {
        $content = Get-Content $_.FullName -Raw -ErrorAction SilentlyContinue
        if ($content -match '\.agent-worktrees\\bin\\launch-session') {
            Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
            Ok "Removed project binstub $($_.Name)"
        }
    }
}

# Windows Terminal fragment + psmux config
if (Test-Path $WtFragment) { Remove-Item $WtFragment -Recurse -Force -ErrorAction SilentlyContinue; Ok 'Removed Windows Terminal fragment' }
if (Test-Path $PsmuxConf)  { Remove-Item $PsmuxConf -Force -ErrorAction SilentlyContinue; Ok 'Removed psmux config' }

# -- 5. Optional: per-project config dirs (~/.<project>) -----------------------
if ($RemoveProjectConfigs) {
    Get-ChildItem -Path $Home_ -Directory -Force -ErrorAction SilentlyContinue |
        Where-Object { Test-Path (Join-Path $_.FullName 'config.yaml') } |
        ForEach-Object {
            $cfg = Get-Content (Join-Path $_.FullName 'config.yaml') -Raw -ErrorAction SilentlyContinue
            if ($cfg -match 'worktree_root|anchor:') {
                Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
                Ok "Removed project config $($_.Name)"
            }
        }
}

# -- 6. Optional: marketplace plugins ------------------------------------------
if ($RemovePlugins) {
    if (Get-Command copilot -ErrorAction SilentlyContinue) {
        foreach ($name in $Binstubs) {
            Step "copilot plugin uninstall $name"
            & copilot plugin uninstall "$name@copilot-extensions" *> $null
        }
        & copilot plugin marketplace remove ThomasMichon/copilot-extensions *> $null
        Ok 'Marketplace plugins uninstalled'
    } else {
        Warn 'copilot CLI not found -- skipping plugin uninstall'
    }
}

# -- 7. Report leftovers -------------------------------------------------------
Write-Host ''
$leftovers = @()
foreach ($rt in $Runtimes) { if (Test-Path $rt) { $leftovers += $rt } }
foreach ($port in $Ports) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { $leftovers += "port $port still listening" }
}
if ($leftovers.Count -eq 0) {
    Ok 'Baseline reset complete -- no copilot-extensions runtime artifacts remain'
} else {
    Warn 'Some artifacts remain:'
    $leftovers | ForEach-Object { Write-Host "    - $_" }
}
Write-Host ''
