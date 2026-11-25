param(
    [Parameter(Mandatory)][string]$Root,
    [Parameter(Mandatory)][string]$Uv,
    [string]$LegacyPython = ''
)
$ErrorActionPreference = 'Stop'
$IndexServicePython = $null
foreach ($name in @('resolve-runtime.ps1', 'bootstrap-uv.ps1',
        'bootstrap-capture.ps1', 'bootstrap-capture.exe')) {
    if (-not [IO.File]::Exists((Join-Path $PSScriptRoot $name))) {
        throw 'Index service requires prepared runtime helpers; use explicit installation management.'
    }
}
if (-not [IO.Path]::IsPathRooted($Uv) -or -not [IO.File]::Exists($Uv)) {
    throw 'Index service requires prepared UV prerequisites; use explicit installation management.'
}
$previousRoot = $env:AGENT_RT_ROOT
try {
    $env:AGENT_RT_ROOT = $Root
    . (Join-Path $PSScriptRoot 'resolve-runtime.ps1') -TraceMode read-only
    $selectedPython = $AgentRtPy
} finally {
    if ($null -eq $previousRoot) {
        Remove-Item Env:AGENT_RT_ROOT -ErrorAction SilentlyContinue
    } else { $env:AGENT_RT_ROOT = $previousRoot }
}
# The pre-versioned engine is independent of the primary service runtime.
if (-not $selectedPython -and $LegacyPython -and
    -not (Test-Path -LiteralPath (Join-Path $Root 'current-version')) -and
    -not (Test-Path -LiteralPath (Join-Path $Root 'last-known-good')) -and
    -not (Test-Path -LiteralPath (Join-Path $Root 'versions'))) {
    $selectedPython = $LegacyPython
}
if (-not $selectedPython) {
    throw 'No completed Index runtime is selectable; use explicit installation management.'
}
. (Join-Path $PSScriptRoot 'bootstrap-capture.ps1')
. (Join-Path $PSScriptRoot 'bootstrap-uv.ps1')
$invoke = {
    param([string]$FilePath, [string[]]$ArgList, [hashtable]$Environment)
    Invoke-BootstrapOwnedCapture -FilePath $FilePath -ArgList $ArgList `
        -Environment $Environment -TimeoutMilliseconds 10000 -CleanupMilliseconds 500
}
$selection = Resolve-UvRuntimePython -ExpectedPython $selectedPython -UvPath $Uv `
    -InvokeCaptured $invoke
if ($selection.Status -cne 'selected') {
    throw 'Index service requires prepared UV prerequisites; use explicit installation management.'
}
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
$IndexServicePython = $selection.Python
