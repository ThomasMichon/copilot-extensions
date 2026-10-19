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
$mode = & (Join-Path $PSScriptRoot 'payload-mode.ps1')
if ($mode -ceq 'cell') {
    if ($InstallDir -or $Force) { throw 'Cell bootstrap cannot use legacy -InstallDir or -Force' }
    & (Join-Path $PSScriptRoot 'cell-install.ps1') bootstrap
    exit $LASTEXITCODE
}
if ($mode -cne 'legacy') { throw 'Native installation admission returned an unknown mode' }
$argsList = @('install')
if ($InstallDir) { $argsList += @('-InstallDir', $InstallDir) }
if ($Force) { $argsList += '-Force' }
& (Join-Path $PSScriptRoot 'install.ps1') @argsList
exit $LASTEXITCODE
