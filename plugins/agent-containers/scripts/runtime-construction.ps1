param(
    [Parameter(Mandatory)][string]$Uv,
    [Parameter(Mandatory)][string]$VenvDir
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
function Write-Step { param([string]$Message) [Console]::Error.WriteLine($Message) }
function Write-Ok { param([string]$Message) [Console]::Error.WriteLine($Message) }
function Write-Warn { param([string]$Message) [Console]::Error.WriteLine($Message) }
$support = if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'installer-engine.ps1')) {
    $PSScriptRoot
} else {
    [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../libs/installer-engine'))
}
. (Join-Path $support 'bootstrap-uv.ps1')
. (Join-Path $support 'installer-engine.ps1')
$environment = Get-UvBootstrapEnvironment
$environment.UV_CONFIG_FILE = $null
$environment.UV_NO_CONFIG = '1'
foreach ($name in $environment.Keys) {
    [Environment]::SetEnvironmentVariable($name, $environment[$name], 'Process')
}
$python = Join-Path $VenvDir 'Scripts/python.exe'
if (-not (New-SignedVenv -VenvDir $VenvDir -VenvPython $python -UvCommand $Uv `
    -PythonVersion '3.11' -MinimumSignedBaseVersion ([version]'3.11') `
    -AllowExisting $false -WithoutPip)) {
    throw 'Managed Containers application environment construction failed.'
}
