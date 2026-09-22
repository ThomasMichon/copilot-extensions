<#
.SYNOPSIS
    init.ps1 -- thin compatibility shim.
#>
[CmdletBinding()]
param(
    [string]$InstallDir,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
$argsList = @('install')
if ($InstallDir) { $argsList += @('-InstallDir', $InstallDir) }
if ($Force) { $argsList += '-Force' }
& (Join-Path $PSScriptRoot 'install.ps1') @argsList
exit $LASTEXITCODE
