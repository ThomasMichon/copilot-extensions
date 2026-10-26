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
$python = $null
foreach ($name in @('python', 'python3', 'py')) {
    $candidate = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($candidate) {
        $output = Invoke-Hidden $candidate.Source @('-I', '-B', '-c', 'import sys; assert sys.version_info >= (3,10); print(sys.executable)')
        if ($LASTEXITCODE -eq 0) { $python = $output.Trim(); break }
    }
}
if (-not $python) { throw 'Legacy uninstall requires an existing external Python 3.10+ interpreter' }
$argsForHelper = @(
    '-I', '-B', $helper, '--payload-root', (Split-Path -Parent $PSScriptRoot),
    '--plugin-id', 'agent-machines', '--root', $InstallDir,
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
