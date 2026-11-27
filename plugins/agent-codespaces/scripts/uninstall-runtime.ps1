function Get-UninstallPython {
    if (-not (Get-Command Resolve-NativeBootstrapPython -CommandType Function -ErrorAction SilentlyContinue)) {
        . (Join-Path $PSScriptRoot 'versioned_runtime_native.ps1')
    }
    $support = $PSScriptRoot
    if ([IO.File]::ReadAllLines((Join-Path $PSScriptRoot 'install.sh')) -ccontains
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"') {
        $support = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../libs/installer-engine'))
    }
    $reader = Get-Command Resolve-NativeBootstrapPython -CommandType Function
    if (-not $reader.Parameters.ContainsKey('InvokeCaptured')) {
        throw 'Native bootstrap reader lacks bounded capture; restore the owning payload.'
    }
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        $captureSupport = if (Test-Path -LiteralPath (Join-Path $support 'bootstrap-capture.ps1')) {
            $support
        } else {
            Join-Path $PSScriptRoot '_bootstrap_python'
        }
        . (Join-Path $captureSupport 'bootstrap-capture.ps1')
    }
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $budget = 30000
    $capture = {
        param([string]$FilePath, [string[]]$ArgList, [hashtable]$Environment)
        $remaining = $budget - $clock.ElapsedMilliseconds
        if ($remaining -le 1) { throw [TimeoutException]::new('WATCHDOG: uninstall bootstrap deadline.') }
        $cleanup = [int][Math]::Min(3000, [Math]::Max(1, [Math]::Floor($remaining / 4)))
        if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
            return Invoke-BootstrapOwnedCapture -FilePath $FilePath -ArgList $ArgList `
                -Environment $Environment -TimeoutMilliseconds ([int]$remaining) -CleanupMilliseconds $cleanup
        }
        $diagnostic = ''
        $output = Invoke-Hidden -FilePath $FilePath -ArgList $ArgList -Environment $Environment `
            -InputText '' -ErrorOutput ([ref]$diagnostic) `
            -TimeoutMilliseconds ([int]$remaining) -CleanupMilliseconds $cleanup
        [pscustomobject]@{ ExitCode = $LASTEXITCODE; Stdout = [string]$output; Stderr = $diagnostic }
    }
    return Resolve-NativeBootstrapPython -Action uninstall -Root $InstallDir `
        -BootstrapDirectory $support -InvokeCaptured $capture
}

function Invoke-StatePreservingUninstall {
    param([switch]$Preview, [switch]$AdmissionOnly)
    $python = Get-UninstallPython
    if (-not $python) { throw 'Uninstall requires the prepared uv-managed bootstrap Python outside runtime and prefix closure; nothing removed' }
    if (Get-ScheduledTask -ErrorAction Stop | Where-Object { $_.TaskName -eq $OwnerTaskName }) {
        throw 'Uninstall refuses Connection Owner service custody; runtime and transports left intact'
    }
    $arguments = @('-I', '-B', (Join-Path $PSScriptRoot 'uninstall-state.py'), $InstallDir,
        '--shell', (Get-Process -Id $PID).Path)
    if ($Preview -or $AdmissionOnly) { $arguments += '--preview' }
    $nativeError = ''
    $output = Invoke-Hidden -FilePath $python -ArgList $arguments -ErrorOutput ([ref]$nativeError)
    if ($LASTEXITCODE -ne 0) { throw "Uninstall admission refused: $output$nativeError" }
    if (-not $AdmissionOnly) { Write-Host $output }
}
