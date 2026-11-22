[CmdletBinding()]
param(
    [ValidateSet('Inspect', 'Prepare')][string]$Operation = 'Inspect',
    [ValidateRange(1, 2147483647)][int]$TimeoutMilliseconds = 30000
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'bootstrap-uv.ps1')
if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
    . (Join-Path $PSScriptRoot 'bootstrap-capture.ps1')
} else {
    . (Join-Path $PSScriptRoot 'versioned_runtime_native.ps1')
}
$bootstrapClock = [Diagnostics.Stopwatch]::StartNew()
$capture = {
    param([string]$FilePath, [string[]]$ArgList, [hashtable]$Environment)
    $remaining = $TimeoutMilliseconds - $bootstrapClock.ElapsedMilliseconds
    if ($remaining -le 1) {
        throw [TimeoutException]::new('WATCHDOG: command bootstrap Python selection timed out.')
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
    -RequestFile (Join-Path (Split-Path -Parent $PSScriptRoot) 'bootstrap-python-version.txt') `
    -Operation $Operation -InvokeCaptured $capture -IsolateSelection
if ($selection.Status -cne 'selected') {
    throw (
        'Command bootstrap Python is unavailable (' + $selection.Reason + '). ' +
        'Prepare PATH uv and run the owning _bootstrap_python\select.ps1 -Operation Prepare. ' +
        'Ordinary commands do not download Python or fall back to PATH Python.'
    )
}
$selection.Python
