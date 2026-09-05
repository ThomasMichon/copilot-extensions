<#
.SYNOPSIS
    init.ps1 -- thin compatibility shim.
.DESCRIPTION
    The canonical installer is scripts/install.ps1. This bootstrap alias forwards
    to `install.ps1 -Action install` so older references and the runtime
    reconciler's init fallback keep working. Only the lightweight client is
    installed; the host service is exclusively dispatch-managed.
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
