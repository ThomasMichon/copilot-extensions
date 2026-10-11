# Only the dependency-light metadata adapter is admitted at session start.
& (Join-Path $PSScriptRoot 'runtime-gate.ps1') __session-stamp | Out-Null
$rc = $LASTEXITCODE
[Console]::Out.Write('{}')
exit $rc
