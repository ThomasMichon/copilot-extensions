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
    $probe = @'
import sys
from pathlib import Path
assert sys.version_info >= (3, 10)
root = Path(sys.argv[1]).absolute()
assert ".." not in root.parts
roots = (root, root.resolve())
paths = (sys.argv[2], sys.executable, sys.prefix, sys.base_prefix,
         getattr(sys, "_base_executable", sys.executable))
for value in paths:
    path = Path(value).absolute()
    for actual in (path, path.resolve(strict=True)):
        assert all(not actual.is_relative_to(boundary) for boundary in roots)
print(Path(sys.executable).resolve(strict=True))
'@
    $candidates = @()
    foreach ($name in @('python3', 'python')) {
        $candidates += @(Get-Command $name -All -CommandType Application -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -notmatch 'WindowsApps' } | ForEach-Object { $_.Source })
    }
    foreach ($launcher in @(Get-Command py -All -CommandType Application -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -notmatch 'WindowsApps' })) {
        if ($launcher.Source.StartsWith($InstallDir.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { continue }
        try {
            $nativeError = ''
            $listed = Invoke-Hidden -FilePath $launcher.Source -ArgList @('-0p') -ErrorOutput ([ref]$nativeError)
            if ($LASTEXITCODE -eq 0) {
                foreach ($match in [regex]::Matches("$listed", '(?m)([A-Za-z]:[\\/].*python(?:[0-9.]*)?\.exe)"?\s*$')) {
                    $candidates += $match.Groups[1].Value
                }
            }
        } catch { }
    }
    foreach ($candidate in @($candidates | Select-Object -Unique)) {
        if ($candidate.StartsWith($InstallDir.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { continue }
        try {
            $nativeError = ''
            $selected = Invoke-Hidden -FilePath $candidate -ArgList @(
                '-I', '-B', '-c', $probe, $InstallDir, $candidate
            ) -ErrorOutput ([ref]$nativeError)
            if ($LASTEXITCODE -eq 0 -and $selected -and [IO.File]::Exists($selected.Trim())) {
                return $selected.Trim()
            }
        } catch { }
    }
    return $null
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
