# Only the dependency-light metadata adapter is admitted at session start.
function Exit-SessionStart {
    param([int]$Code)
    [Console]::Out.Write('{}')
    exit $Code
}
if (-not $env:COPILOT_EXTENSIONS_CONTEXT) {
    $_bksGuard = Join-Path $PSScriptRoot 'bootstrap-killswitch-guard.ps1'
    try {
        if (-not (Test-Path -LiteralPath $_bksGuard -PathType Leaf)) {
            throw "bootstrap-killswitch guard missing: $_bksGuard"
        }
        $guardTokens = $null
        $guardErrors = $null
        $null = [System.Management.Automation.Language.Parser]::ParseFile(
            $_bksGuard, [ref]$guardTokens, [ref]$guardErrors)
        if ($guardErrors.Count -gt 0) {
            throw "bootstrap-killswitch guard malformed: $($guardErrors[0].Message)"
        }
        $global:LASTEXITCODE = $null
        & $_bksGuard check | Out-Null
        if ($global:LASTEXITCODE -eq 0) { Exit-SessionStart 0 }
        if ($global:LASTEXITCODE -ne 1) {
            throw "bootstrap-killswitch guard failed (exit $global:LASTEXITCODE)"
        }
    } catch {
        [Console]::Error.WriteLine("agent-index bootstrap-killswitch guard failed: $_")
        Exit-SessionStart 1
    }
}
try {
    & (Join-Path $PSScriptRoot 'runtime-gate.ps1') __session-stamp | Out-Null
    $rc = $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine("agent-index session runtime gate failed: $_")
    Exit-SessionStart 1
}
Exit-SessionStart $rc
