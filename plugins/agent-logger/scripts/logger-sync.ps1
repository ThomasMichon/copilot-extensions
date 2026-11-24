$ErrorActionPreference = 'Stop'
if ($args.Count) { throw 'Scheduled Logger sync does not accept command arguments.' }
$_root = Split-Path -Parent $PSScriptRoot
foreach ($_support in @('resolve-runtime.ps1', 'bootstrap-uv.ps1', 'bootstrap-capture.ps1', 'bootstrap-capture.exe')) {
    if (-not [IO.File]::Exists((Join-Path $PSScriptRoot $_support))) {
        throw 'Logger sync support unavailable; use explicit installation management.'
    }
}
$env:AGENT_RT_ROOT = $_root
$AgentRtPy = $null
. (Join-Path $PSScriptRoot 'resolve-runtime.ps1') -TraceMode read-only
if (-not $AgentRtPy) {
    throw 'Logger sync runtime unavailable; use explicit installation management.'
}
. (Join-Path $PSScriptRoot 'bootstrap-capture.ps1')
. (Join-Path $PSScriptRoot 'bootstrap-uv.ps1')
$_invoke = {
    param([string]$FilePath, [string[]]$ArgList, [hashtable]$Environment)
    Invoke-BootstrapOwnedCapture -FilePath $FilePath -ArgList $ArgList `
        -Environment $Environment -TimeoutMilliseconds 10000 -CleanupMilliseconds 500
}
$_selection = Resolve-UvRuntimePython -ExpectedPython $AgentRtPy `
    -UvPath '__PREPARED_UV__' -InvokeCaptured $_invoke
if ($_selection.Status -cne 'selected') {
    throw 'Logger sync requires prepared UV prerequisites; use explicit installation management.'
}
$env:PYTHONUTF8 = '1'
$env:AGENT_LOGGER_HOME = $_root
__REPO_CONFIG_LINE__
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
$_previousLocation = Get-Location
try {
    Set-Location -LiteralPath $_root
    $ErrorActionPreference = 'Continue'
    $LASTEXITCODE = 1
    & $_selection.Python -I -B -X utf8 -m agent_logger.sync.engine run --prune
    $_exitCode = $LASTEXITCODE
} finally {
    Set-Location -LiteralPath $_previousLocation.ProviderPath
}
exit $_exitCode
