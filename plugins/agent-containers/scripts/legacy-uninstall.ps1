[CmdletBinding()]
param([Parameter(Mandatory)][string]$InstallDir)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'versioned_runtime_native.ps1')
. (Join-Path $PSScriptRoot 'legacy-transaction.ps1')
$InstallDir = Resolve-LegacyTransactionRoot $InstallDir
$helper = Join-Path $PSScriptRoot 'installer_engine_uninstall.py'
if (-not [IO.File]::Exists($helper)) {
    throw 'Required canonical finite removal helper is unavailable; refusing any purge fallback'
}
function Get-LegacyUninstallPython {
    $support = $PSScriptRoot
    if (-not (Test-Path -LiteralPath (Join-Path $support 'bootstrap-uv.ps1')) -and
        -not (Test-Path -LiteralPath (Join-Path $support 'installer-instance-python-version.txt'))) {
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
    return Resolve-NativeBootstrapPython -MinimumMinor 10 -Action uninstall `
        -Root $InstallDir -BootstrapDirectory $support -InvokeCaptured $capture
}
$python = Get-LegacyUninstallPython
if (-not $python) { throw 'Legacy uninstall requires an existing external Python 3.10+ interpreter' }
$argsForHelper = @(
    '-I', '-B', $helper, '--payload-root', (Split-Path -Parent $PSScriptRoot),
    '--plugin-id', 'agent-containers', '--root', $InstallDir,
    '--shell', (Get-Process -Id $PID).Path,
    '--profile', (Join-Path $PSScriptRoot 'uninstall-profile.json')
)
$result = Invoke-Hidden $python ($argsForHelper + @('--check'))
if ($LASTEXITCODE -ne 0) { throw "Legacy uninstall admission refused: $result" }
if (-not [IO.Directory]::Exists($InstallDir)) { exit 0 }
$lease = Enter-LegacyTransaction $InstallDir
try {
    $result = Invoke-Hidden $python ($argsForHelper + @('--remove-runtime'))
    if ($LASTEXITCODE -ne 0) { throw "Legacy uninstall refused: $result" }
    Write-Host 'Runtime content removed; durable state and unknown files retained.'
} finally {
    $lease.Dispose()
}
