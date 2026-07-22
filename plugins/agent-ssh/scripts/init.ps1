# Back-compat bootstrap entrypoint: delegate to the canonical install script.
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
