<#
.SYNOPSIS
    Bootstrap the agent-codespaces runtime (delegates to install.ps1).

.DESCRIPTION
    Backwards-compatible shim. The canonical, self-contained install flow lives
    in install.ps1 (the uv pip install model). This forwards to it so there is a
    single deploy path per the install contract (docs/install-contract.md).

.PARAMETER InstallDir
    Accepted for compatibility; ignored. The runtime is always ~/.agent-codespaces.

.PARAMETER Force
    Accepted for compatibility; ignored (install.ps1 is idempotent).
#>
[CmdletBinding()]
param(
    [string]$InstallDir,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($InstallDir) {
    Write-Warning "init.ps1 no longer supports -InstallDir; using ~/.agent-codespaces."
}
& (Join-Path $ScriptDir 'install.ps1') install
exit $LASTEXITCODE