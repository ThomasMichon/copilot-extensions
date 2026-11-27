function Get-CellLifecyclePython {
    $support = $PSScriptRoot
    if (-not (Test-Path -LiteralPath (Join-Path $support 'bootstrap-uv.ps1')) -and
        -not (Test-Path -LiteralPath (Join-Path $support 'installer-instance-python-version.txt'))) {
        $support = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../libs/installer-engine'))
    }
    . (Join-Path $support 'bootstrap-uv.ps1')
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        . (Join-Path $support 'bootstrap-capture.ps1')
    }
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $capture = {
        param([string]$FilePath, [string[]]$ArgList, [hashtable]$Environment)
        $remaining = 30000 - $clock.ElapsedMilliseconds
        if ($remaining -le 1) {
            throw [TimeoutException]::new('WATCHDOG: cell lifecycle bootstrap selection timed out.')
        }
        $cleanup = [int][Math]::Min(3000, [Math]::Max(1, [Math]::Floor($remaining / 4)))
        if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
            return Invoke-BootstrapOwnedCapture -FilePath $FilePath -ArgList $ArgList `
                -Environment $Environment -TimeoutMilliseconds ([int]$remaining) `
                -CleanupMilliseconds $cleanup
        }
        $diagnostic = ''
        $output = Invoke-Hidden -FilePath $FilePath -ArgList $ArgList -Environment $Environment `
            -InputText '' -ErrorOutput ([ref]$diagnostic) `
            -TimeoutMilliseconds ([int]$remaining) -CleanupMilliseconds $cleanup
        [pscustomobject]@{
            ExitCode = $LASTEXITCODE; Stdout = [string]$output; Stderr = $diagnostic
        }
    }
    $selection = Resolve-UvBootstrapPython `
        -RequestFile (Join-Path $support 'installer-instance-python-version.txt') `
        -Operation Inspect -InvokeCaptured $capture -IsolateSelection
    if ($selection.Status -cne 'selected') {
        throw (
            'Cell lifecycle requires prepared uv-managed installer Python (' + $selection.Reason + '). ' +
            'Run prerequisite setup. No installation or PATH Python fallback was attempted.'
        )
    }
    $probe = & $capture -FilePath $selection.Python -ArgList @(
        '-I', '-S', '-B', '-c', 'import sys; sys.exit(sys.version_info < (3, 10))'
    ) -Environment (Get-UvBootstrapEnvironment)
    if ($probe.ExitCode -ne 0) { throw 'Cell lifecycle requires prepared Python 3.10 or newer.' }
    $selection.Python
}
