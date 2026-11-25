param([switch]$EnsureSelected)
$ErrorActionPreference = 'Stop'
if ($EnsureSelected -and $args.Count) { throw 'Selected-owner ensure does not accept command arguments.' }
$_arguments = @($args)
$_root = Split-Path -Parent $PSScriptRoot
$_resolver = Join-Path $PSScriptRoot 'resolve-runtime.ps1'
foreach ($_support in @('resolve-runtime.ps1', 'bootstrap-uv.ps1', 'bootstrap-capture.ps1', 'bootstrap-capture.exe')) {
    if (-not [IO.File]::Exists((Join-Path $PSScriptRoot $_support))) {
        throw 'Codespaces owner support unavailable; use explicit installation management.'
    }
}
$env:AGENT_RT_ROOT = $_root
$AgentRtPy = $null
. $_resolver -TraceMode read-only
if (-not $AgentRtPy) {
    throw 'Codespaces owner runtime unavailable; use explicit installation management.'
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
    throw 'Codespaces owner requires prepared UV prerequisites; use explicit installation management.'
}
$env:PYTHONUTF8 = '1'
$env:AGENT_CODESPACES_HOME = $_root
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
$_previousLocation = Get-Location
try {
    Set-Location -LiteralPath $_root
    $ErrorActionPreference = 'Continue'
    if ($EnsureSelected) {
        & $_selection.Python -I -B -X utf8 -c 'from agent_codespaces.connection_owner import ensure_owner_running; raise SystemExit(0 if ensure_owner_running(None) else 1)'
    } elseif ($PSVersionTable.PSVersion -lt [version]'7.3' -or
        (Get-Variable PSNativeCommandArgumentPassing -ValueOnly -ErrorAction SilentlyContinue) -eq 'Legacy') {
        $_json = ConvertTo-Json -InputObject $_arguments -Compress
        $_encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($_json))
        $_bootstrap = "import base64,json,runpy,sys;sys.argv=['agent_codespaces']+json.loads(base64.b64decode(sys.argv[1]));runpy.run_module('agent_codespaces',run_name='__main__',alter_sys=True)"
        & $_selection.Python -I -B -X utf8 -c $_bootstrap $_encoded
    } else {
        & $_selection.Python -I -B -X utf8 -m agent_codespaces @_arguments
    }
    $_exitCode = $LASTEXITCODE
} finally {
    Set-Location -LiteralPath $_previousLocation.ProviderPath
}
exit $_exitCode
