$ErrorActionPreference = 'Stop'
$forward = @($args)
$mode = & (Join-Path $PSScriptRoot 'payload-mode.ps1')
if ($mode -ceq 'legacy') {
    $env:AGENT_RT_ROOT = Join-Path $env:USERPROFILE '.agent-pull-requests'
    . (Join-Path $PSScriptRoot 'resolve-runtime.ps1')
    if (-not $AgentRtPy) {
        if (Test-Path 'env:AGENT_PULL_REQUESTS_NO_SELFPROVISION') {
            throw 'Runtime not provisioned (AGENT_PULL_REQUESTS_NO_SELFPROVISION set)'
        }
        $hostExe = (Get-Process -Id $PID).Path
        & $hostExe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `
            (Join-Path $PSScriptRoot 'install.ps1') provision 2>&1 |
            ForEach-Object { [Console]::Error.WriteLine($_) }
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        . (Join-Path $PSScriptRoot 'resolve-runtime.ps1')
    }
    if (-not $AgentRtPy) { throw 'Owning installer returned without a resolvable runtime' }
    $ErrorActionPreference = 'Continue'
    & $AgentRtPy -m agent_pull_requests @forward
    exit $LASTEXITCODE
}
if ($mode -cne 'cell') { throw 'Native installation admission returned an unknown mode' }
$python = $null
foreach ($candidate in @('python3', 'python')) {
    $command = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notmatch 'WindowsApps') {
        & $command.Source -I -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>$null
        if ($LASTEXITCODE -eq 0) { $python = $command.Source; break }
    }
}
if (-not $python) { throw 'Governed invocation requires an existing Python 3.10+ interpreter' }
$ErrorActionPreference = 'Continue'
& $python -I (Join-Path $PSScriptRoot 'cell_invoke.py') -- @forward
exit $LASTEXITCODE
