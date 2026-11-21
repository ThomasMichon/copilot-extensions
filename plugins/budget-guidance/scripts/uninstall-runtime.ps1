function Get-FiniteUninstallPython {
    param([Parameter(Mandatory)][string]$InstallRoot)
    $probe = @'
import sys
assert sys.version_info >= (3, 10)
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
    . (Join-Path $PSScriptRoot 'bootstrap-uv.ps1')
    $capture = {
        param($FilePath, $ArgList, $Environment)
        $nativeError = ''
        $output = Invoke-Hidden -FilePath $FilePath -ArgList $ArgList `
            -Environment $Environment -ErrorOutput ([ref]$nativeError)
        [pscustomobject]@{ExitCode=$LASTEXITCODE; Stdout=[string]$output; Stderr=$nativeError}
    }
    $bootstrap = Resolve-UvBootstrapPython `
        -RequestFile (Join-Path $PSScriptRoot 'bootstrap-python-version.txt') `
        -InvokeCaptured $capture -Operation Inspect -IsolateSelection
    if ($bootstrap.Status -cne 'selected') {
        throw ('Uninstall requires prepared managed Python; run explicit setup: ' + $bootstrap.Reason)
    }
    $nativeError = ''
    $selected = Invoke-Hidden -FilePath $bootstrap.Python -ArgList @(
        '-I', '-B', '-c', $probe, $InstallRoot, $bootstrap.Python
    ) -ErrorOutput ([ref]$nativeError)
    if ($LASTEXITCODE -eq 0 -and $selected -and [IO.File]::Exists($selected.Trim())) {
        return $selected.Trim()
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
    if (-not $python) { throw 'Runtime uninstall requires prepared managed Python outside runtime and prefix closure' }
    $mode = if ($Check) { '--check' } else { '--remove-runtime' }
    $output = Invoke-Hidden -FilePath $python -ArgList @(
        '-I', '-B', $helper, '--payload-root', (Split-Path -Parent $PSScriptRoot),
        '--plugin-id', 'budget-guidance', '--root', $InstallRoot,
        '--shell', (Get-Process -Id $PID).Path, $mode)
    if ($LASTEXITCODE -ne 0) { throw 'Finite runtime uninstall refused; see native diagnostic' }
    if ($output) { Write-Host $output.Trim() }
}
