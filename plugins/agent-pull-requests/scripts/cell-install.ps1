# Explicit cell adapter; no self-provisioning or legacy-root fallback.
$ErrorActionPreference = 'Stop'
$python = $null
foreach ($candidate in @('python3', 'python')) {
    $command = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notmatch 'WindowsApps') {
        & $command.Source -I -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>$null
        if ($LASTEXITCODE -eq 0) { $python = $command.Source; break }
    }
}
if (-not $python) { throw 'Cell installer requires an existing Python 3.10+ interpreter' }
$workingDirectory = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
Set-Location -LiteralPath $workingDirectory
[IO.Directory]::SetCurrentDirectory($workingDirectory)
if ($args.Count -eq 1 -and $args[0] -ceq 'bootstrap') {
    & $python -I (Join-Path $PSScriptRoot 'cell_invoke.py') --bootstrap
    exit $LASTEXITCODE
}
& $python -I (Join-Path $PSScriptRoot 'cell_install.py') @args
exit $LASTEXITCODE
