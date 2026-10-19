# Native admission wrapper for the unchanged shared bootstrap-check reconciler.
$ErrorActionPreference = 'Stop'
try {
    $mode = & (Join-Path $PSScriptRoot 'payload-mode.ps1')
} catch {
    [Console]::Error.WriteLine("Bootstrap admission refused: $($_.Exception.Message)")
    [Console]::Out.Write('{}')
    exit 126
}
if ($mode -ceq 'legacy') {
    & (Join-Path $PSScriptRoot 'bootstrap-check.ps1')
    exit $LASTEXITCODE
}
if ($mode -cne 'cell') { throw 'Native installation admission returned an unknown mode' }
# Provision and activation are on demand through the owning payload, not session start.
[Console]::Out.Write('{}')
