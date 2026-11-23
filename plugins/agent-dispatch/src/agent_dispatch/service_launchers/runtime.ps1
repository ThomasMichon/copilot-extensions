. (Join-Path $PSScriptRoot 'versioned_runtime_native.ps1')
. (Join-Path $PSScriptRoot 'bootstrap-uv.ps1')

function Resolve-DispatchServicePython {
    param([Parameter(Mandatory)][string]$RuntimePython)
    $invoke = {
        param([string]$FilePath, [string[]]$ArgList, [hashtable]$Environment)
        $errors = ''
        $output = Invoke-Hidden -FilePath $FilePath -ArgList $ArgList -Environment $Environment `
            -ErrorOutput ([ref]$errors) -TimeoutMilliseconds 10000 `
            -CaptureEncoding ([Text.UTF8Encoding]::new($false))
        [pscustomobject]@{
            ExitCode = $LASTEXITCODE; Stdout = [string]$output; Stderr = $errors
        }
    }
    $selected = Resolve-UvRuntimePython -ExpectedPython $RuntimePython -InvokeCaptured $invoke
    if ($selected.Status -cne 'selected') {
        throw 'Dispatch service requires prepared UV prerequisites; use explicit installation management.'
    }
    return $selected.Python
}
