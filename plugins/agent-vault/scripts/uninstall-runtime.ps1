function Get-FiniteUninstallPython {
    param([Parameter(Mandatory)][string]$InstallRoot)
    $probe = @'
import sys
assert sys.version_info >= (3, 11)
from pathlib import Path
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
        try {
            $nativeError = ''
            $selected = Invoke-Hidden -FilePath $candidate -ArgList @(
                '-I', '-B', '-c', $probe, $InstallRoot, $candidate
            ) -ErrorOutput ([ref]$nativeError)
            if ($LASTEXITCODE -eq 0 -and $selected -and [IO.File]::Exists($selected.Trim())) {
                return $selected.Trim()
            }
        } catch { }
    }
    return $null
}

function Invoke-FiniteRuntimeUninstall {
    param([string]$InstallRoot, [switch]$Check)
    $helper = Join-Path $PSScriptRoot 'installer_engine_uninstall.py'
    if (-not (Test-Path -LiteralPath $helper)) {
        $helper = Join-Path $PSScriptRoot '..\..\..\libs\installer-engine\installer_engine_uninstall.py'
    }
    $python = Get-FiniteUninstallPython -InstallRoot $InstallRoot
    if (-not $python) { throw 'Native install and uninstall require an existing external Python 3.11+ outside runtime and prefix closure' }
    $mode = if ($Check) { '--check' } else { '--remove-runtime' }
    $output = Invoke-Hidden -FilePath $python -ArgList @(
        '-I', '-B', $helper, '--payload-root', (Split-Path -Parent $PSScriptRoot),
        '--plugin-id', 'agent-vault', '--root', $InstallRoot,
        '--shell', (Get-Process -Id $PID).Path, $mode)
    if ($LASTEXITCODE -ne 0) { throw 'Finite runtime uninstall refused; see native diagnostic' }
    if ($output) { Write-Host $output.Trim() }
}
