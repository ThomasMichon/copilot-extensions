. (Join-Path $PSScriptRoot 'bootstrap-capture.ps1')
. (Join-Path $PSScriptRoot 'bootstrap-uv.ps1')

function Resolve-DispatchServicePython {
    param(
        [Parameter(Mandatory)][string]$RuntimePython,
        [ValidateRange(1001, 10000)][int]$TimeoutMilliseconds = 10000
    )
    $invoke = {
        param([string]$FilePath, [string[]]$ArgList, [hashtable]$Environment)
        Invoke-BootstrapOwnedCapture -FilePath $FilePath -ArgList $ArgList `
            -Environment $Environment -TimeoutMilliseconds $TimeoutMilliseconds `
            -CleanupMilliseconds 500
    }
    $selected = Resolve-UvRuntimePython -ExpectedPython $RuntimePython -InvokeCaptured $invoke
    if ($selected.Status -cne 'selected') {
        throw 'Dispatch service requires prepared UV prerequisites; use explicit installation management.'
    }
    return $selected.Python
}
