<#
.SYNOPSIS
    init.ps1 -- thin compatibility shim.
.DESCRIPTION
    The canonical installer is scripts/install.ps1 (a full lifecycle manager:
    install|update|status|start|stop|uninstall, matching agent-bridge). This
    bootstrap alias forwards to `install.ps1 -Action install` so older
    references and the agent-worktrees reconciler's init fallback keep working.
    Flags pass through (e.g. -NoService, -InstallDir DIR).
#>
[CmdletBinding()]
param(
    [string]$InstallDir,
    [switch]$NoService,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
$installer = Join-Path $PSScriptRoot 'install.ps1'
$fwd = @{ Action = 'install' }
if ($InstallDir) { $fwd['InstallDir'] = $InstallDir }
if ($NoService)  { $fwd['NoService']  = $true }
if ($Force)      { $fwd['Force']      = $true }
& $installer @fwd
exit $LASTEXITCODE
