function Get-UninstallPython {
    # Construction prefers the installed slot; removal must never use that slot.
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
    # Inventory installed launcher targets, then invoke the target itself.
    # Unlike `py -3`, listing cannot provision a missing interpreter.
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
                '-I', '-B', '-c', $probe, $InstallDir, $candidate
            ) -ErrorOutput ([ref]$nativeError)
            if ($LASTEXITCODE -eq 0 -and $selected -and [IO.File]::Exists($selected.Trim())) {
                return $selected.Trim()
            }
        } catch { }
    }
    return $null
}

function Invoke-StatePreservingUninstall {
    param([switch]$Preview, [switch]$AdmissionOnly)
    $python = Get-UninstallPython
    if (-not $python) { throw 'Uninstall requires an existing external Python outside runtime and prefix closure; nothing removed' }
    $arguments = @('-I', '-B', (Join-Path $PSScriptRoot 'uninstall-state.py'), $InstallDir,
        '--shell', (Get-Process -Id $PID).Path)
    if ($Preview -or $AdmissionOnly) { $arguments += '--preview' }
    $nativeError = ''
    $output = Invoke-Hidden -FilePath $python -ArgList $arguments -ErrorOutput ([ref]$nativeError)
    if ($LASTEXITCODE -ne 0) { throw "Uninstall admission refused: $output$nativeError" }
    if (-not $AdmissionOnly) { Write-Host $output }
}
