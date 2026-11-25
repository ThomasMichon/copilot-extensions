param([Parameter(Mandatory)][string]$ScriptsRoot)

$script:LoggerHookClock = [Diagnostics.Stopwatch]::StartNew()
$providerRoot = Join-Path $ScriptsRoot '_bootstrap_python'
$provider = Join-Path $providerRoot 'bootstrap-uv.ps1'
$nativeSupport = Join-Path $providerRoot 'versioned_runtime_native.ps1'
$request = Join-Path $ScriptsRoot 'bootstrap-python-version.txt'
$installer = Join-Path $ScriptsRoot 'install.ps1'
# A partially shipped payload must not borrow a neighboring source checkout.
if (-not (Get-Item -LiteralPath $provider -Force -ErrorAction SilentlyContinue) -and
    -not (Get-Item -LiteralPath $request -Force -ErrorAction SilentlyContinue) -and
    [IO.File]::Exists($installer) -and
    ([IO.File]::ReadAllLines($installer) -ccontains
        ". (Join-Path `$PSScriptRoot '..\..\..\libs\installer-engine\installer-engine.ps1')")) {
    $providerRoot = [IO.Path]::GetFullPath(
        (Join-Path $ScriptsRoot '..\..\..\libs\installer-engine')
    )
    $provider = Join-Path $providerRoot 'bootstrap-uv.ps1'
    $request = Join-Path $providerRoot 'installer-instance-python-version.txt'
    $nativeSupport = Join-Path (Split-Path -Parent $providerRoot) `
        'versioned-runtime\versioned_runtime_native.ps1'
}
. $provider
if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
    . (Join-Path $providerRoot 'bootstrap-capture.ps1')
} else {
    . $nativeSupport
}

function Invoke-LoggerHookCapture {
    param(
        [string]$FilePath, [string[]]$ArgList, [hashtable]$Environment,
        [string]$InputText = ''
    )
    $remaining = 40000 - $script:LoggerHookClock.ElapsedMilliseconds
    if ($remaining -le 1000) { throw 'Session guidance hook deadline expired.' }
    $cleanup = [int][Math]::Min(3000, [Math]::Floor($remaining / 4))
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        return Invoke-BootstrapOwnedCapture -FilePath $FilePath -ArgList $ArgList `
            -Environment $Environment -InputText $InputText `
            -TimeoutMilliseconds ([int]$remaining) -CleanupMilliseconds $cleanup
    }
    $diagnostic = ''
    $output = Invoke-Hidden -FilePath $FilePath -ArgList $ArgList -Environment $Environment `
        -InputText $InputText -ErrorOutput ([ref]$diagnostic) `
        -TimeoutMilliseconds ([int]$remaining) -CleanupMilliseconds $cleanup `
        -CaptureEncoding ([Text.UTF8Encoding]::new($false))
    return [pscustomobject]@{
        ExitCode = $LASTEXITCODE; Stdout = [string]$output; Stderr = $diagnostic
    }
}

$selection = Resolve-UvBootstrapPython -RequestFile $request -Operation Inspect `
    -IsolateSelection -InvokeCaptured {
        param([string]$FilePath, [string[]]$ArgList, [hashtable]$Environment)
        Invoke-LoggerHookCapture -FilePath $FilePath -ArgList $ArgList `
            -Environment $Environment
    }
if ($selection.Status -cne 'selected') { throw 'Prepared hook Python is unavailable.' }
[string]$selection.Python
